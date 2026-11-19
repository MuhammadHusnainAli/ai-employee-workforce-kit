"""Feedback, rule proposals and rules in brain_db.

Each change and its event (`brain.feedback_given`, `brain.change_proposed`, ...) commit in one
tenant transaction. Giving the same feedback twice (same idempotency key) changes nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from core_python.brain.domain import feedback as fb
from core_python.brain.domain.write.model import Candidate, Source
from core_python.brain.remember import Remember


class Invalid(Exception):
    pass


class Given(BaseModel):
    feedback_id: uuid.UUID
    proposal_id: uuid.UUID | None = None
    #: Memories whose importance the rating moved.
    credited: int = 0
    already: bool = False


class Feedback:
    def __init__(self, db: Database, remember: Remember, *, service_source: str) -> None:
        self.db = db
        self.remember = remember
        self.source = service_source

    async def give(self, f: fb.FeedbackIn) -> Given:
        if (why := fb.problem(f)) is not None:
            raise Invalid(why)
        rule = fb.rule_text(f.text) if fb.proposes_rule(f) else None
        if fb.proposes_rule(f) and rule is None:
            raise Invalid("the correction is empty once secrets are removed")
        fid = f.id
        async with self.db.tenant_tx(f.tenant_id) as conn:
            cur = await conn.execute(
                """
                INSERT INTO feedback (id, tenant_id, employee_id, run_id, kind, text, rating,
                    actor_id, actor_kind, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (fid, f.tenant_id, f.employee_id, f.run_id, f.kind, f.text.strip(), f.rating,
                 f.actor_id, f.actor_kind, f.idempotency_key),
            )  # fmt: skip
            if cur.rowcount != 1:
                given = await self._existing(conn, fid)
            else:
                given = Given(feedback_id=fid)
                if rule is not None:
                    given.proposal_id = await self._propose(conn, f, fid, rule)
                if f.kind == "rating" and f.rating is not None:
                    given.credited = await self._credit(conn, f)
                await self._event(conn, f.tenant_id, "brain.feedback_given", f.employee_id,
                                  {"feedback_id": str(fid), "kind": f.kind, "run_id": f.run_id,
                                   "rating": f.rating, "by": f.actor_id})  # fmt: skip
        if f.kind == "approval_rejected" and f.actor_kind == "human":
            # Idempotent on the feedback id, so a retry that finds the feedback stored still
            # makes sure the lesson is there.
            await self.remember(
                Source(event_id=fid, event_type="feedback.approval_rejected",
                       tenant_id=f.tenant_id, employee_id=f.employee_id, run_id=f.run_id,
                       task=f.text, outcome="escalated"),
                [Candidate(kind="reflection", origin="feedback", importance=0.8,
                           confidence=1.0,
                           content=f"A person rejected my approval request: {f.text.strip()}")],
            )  # fmt: skip
        return given

    async def _existing(self, conn: Conn, fid: uuid.UUID) -> Given:
        cur = await conn.execute(
            "SELECT id FROM change_proposals WHERE evidence->>'feedback_id' = %s", (str(fid),)
        )
        row = await cur.fetchone()
        return Given(feedback_id=fid, proposal_id=row["id"] if row else None, already=True)

    async def _propose(self, conn: Conn, f: fb.FeedbackIn, fid: uuid.UUID, rule: str) -> uuid.UUID:
        pid = uuid.uuid5(fid, "proposal")
        await conn.execute(
            """
            INSERT INTO change_proposals (id, tenant_id, employee_id, kind, payload, evidence,
                risk, proposed_by)
            VALUES (%s, %s, %s, 'rule_add', %s, %s, %s, %s)
            """,
            (pid, f.tenant_id, f.employee_id, Jsonb({"text": rule}),
             Jsonb({"feedback_id": str(fid), "run_id": f.run_id}), fb.risk(rule), f.actor_id),
        )  # fmt: skip
        await self._event(conn, f.tenant_id, "brain.change_proposed", f.employee_id,
                          {"proposal_id": str(pid), "kind": "rule_add", "text": rule,
                           "risk": fb.risk(rule)})  # fmt: skip
        return pid

    async def _credit(self, conn: Conn, f: fb.FeedbackIn) -> int:
        """Credit assignment (brain.md section 6): the memories this run recalled gain (or lose)
        importance. Bounded to [0, 1]."""
        step = fb.RATING_STEP * (f.rating or 0)
        cur = await conn.execute(
            """
            UPDATE memories SET importance = least(1.0, greatest(0.0, importance + %s))
             WHERE id IN (SELECT DISTINCT unnest(item_ids) FROM recall_log
                           WHERE run_id = %s AND employee_id = %s)
            """,
            (step, f.run_id, f.employee_id),
        )
        return cur.rowcount

    async def _event(self, conn: Conn, tenant_id: uuid.UUID | str, type_: str,
                     employee_id: str, data: dict[str, Any]) -> None:  # fmt: skip
        await enqueue(
            conn,
            new_event(type_, self.source, tenant_id, data | {"employee_id": employee_id},
                      subject=employee_id),
        )  # fmt: skip
