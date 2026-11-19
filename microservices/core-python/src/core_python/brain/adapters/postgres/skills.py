"""Skills in brain_db: mined from successes, proposed, approved like rules, recalled by trigger.

A mined skill is stored at once with status `proposed`, its trigger already embedded, next to a
`skill_add` change proposal (event `brain.change_proposed`, so evaluation-service runs it). The
person's decision on that proposal flips the skill to `active` or `rejected` in the same
transaction (adapters/postgres/rules.py). Ids derive from the tool sequence, so mining the same
pattern twice, or retrying a step, proposes nothing new.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from core_python.brain.adapters.postgres.memories import vector_literal
from core_python.brain.domain.learn.skills import MATCH, MAX_RECALLED, MinedSkill, Success

#: Successes read per mining pass, newest first.
MAX_SUCCESSES = 500
MINE_DAYS = 90
PROPOSER = "brain-consolidation"


class Skill(BaseModel):
    id: uuid.UUID
    employee_id: str
    name: str
    trigger: str
    tools: list[str]
    procedure: str
    status: str
    proposal_id: uuid.UUID | None
    evidence: list[uuid.UUID]
    approved_by: str | None
    use_count: int
    last_used_at: datetime | None
    created_at: datetime


class RecalledSkill(BaseModel):
    skill_id: str
    name: str
    trigger: str
    tools: list[str]
    procedure: str
    score: float


def skill_id(tenant_id: str, employee_id: str, tools: tuple[str, ...]) -> uuid.UUID:
    return uuid.uuid5(uuid.UUID(tenant_id), f"skill/{employee_id}/{'>'.join(tools)}")


def _vector(text: str | None) -> list[float]:
    if not text:
        return []
    return [float(x) for x in text.strip("[]").split(",") if x]


async def matching(
    conn: Conn, employee_id: str, embedding: list[float], limit: int = MAX_RECALLED
) -> list[RecalledSkill]:
    """Active skills whose trigger is close to the task, best first. Their use is counted."""
    cur = await conn.execute(
        """
        SELECT id, name, trigger, tools, procedure, 1 - (embedding <=> %(v)s::vector) AS score
          FROM skills
         WHERE employee_id = %(e)s AND status = 'active' AND embedding IS NOT NULL
         ORDER BY embedding <=> %(v)s::vector LIMIT %(n)s
        """,
        {"v": vector_literal(embedding), "e": employee_id, "n": limit},
    )
    found = [
        RecalledSkill(skill_id=str(r["id"]), name=r["name"], trigger=r["trigger"],
                      tools=list(r["tools"]), procedure=r["procedure"], score=float(r["score"]))
        for r in await cur.fetchall()
        if float(r["score"]) >= MATCH
    ]  # fmt: skip
    if found:
        await conn.execute(
            "UPDATE skills SET use_count = use_count + 1, last_used_at = now()"
            " WHERE id = ANY(%s::uuid[])",
            ([s.skill_id for s in found],),
        )
    return found


async def candidate(conn: Conn, employee_id: str, proposal_id: str) -> RecalledSkill | None:
    """A proposed skill, for this employee's evaluation and shadow runs. None for anything
    else: unknown, rejected, a rule, another employee's, not a UUID."""
    try:
        uuid.UUID(proposal_id)
    except ValueError:
        return None
    cur = await conn.execute(
        "SELECT s.id, s.name, s.trigger, s.tools, s.procedure FROM skills s"
        " JOIN change_proposals p ON p.id = s.proposal_id"
        " WHERE p.id = %s AND p.employee_id = %s AND p.status <> 'rejected'",
        (proposal_id, employee_id),
    )
    r = await cur.fetchone()
    if r is None:
        return None
    return RecalledSkill(skill_id=str(r["id"]), name=r["name"], trigger=r["trigger"],
                         tools=list(r["tools"]), procedure=r["procedure"], score=1.0)  # fmt: skip


async def decide(conn: Conn, proposal_id: uuid.UUID, *, approve: bool, by: str) -> Skill | None:
    """The person's decision on a skill_add proposal, inside its transaction."""
    cur = await conn.execute(
        "UPDATE skills SET status = %s, approved_by = %s WHERE proposal_id = %s"
        " AND status = 'proposed' RETURNING *",
        ("active" if approve else "rejected", by if approve else None, proposal_id),
    )
    row = await cur.fetchone()
    return Skill.model_validate(row) if row else None


class Skills:
    def __init__(self, db: Database, *, service_source: str) -> None:
        self.db = db
        self.source = service_source

    async def successes(self, tenant_id: str, employee_id: str) -> list[Success]:
        """Recent successful episodes that used tools, with their embeddings."""
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                """
                SELECT m.id, e.task, e.tools, m.embedding::text AS embedding
                  FROM episodes e JOIN memories m ON m.id = e.memory_id
                 WHERE e.employee_id = %s AND e.outcome = 'success' AND m.status = 'active'
                   AND cardinality(e.tools) > 0
                   AND m.recorded_at > now() - make_interval(days => %s)
                 ORDER BY m.recorded_at DESC, m.id LIMIT %s
                """,
                (employee_id, MINE_DAYS, MAX_SUCCESSES),
            )
            rows = await cur.fetchall()
        # Oldest first, so the example and the clusters are stable as new episodes arrive.
        return [
            Success(memory_id=str(r["id"]), task=r["task"], tools=tuple(r["tools"]),
                    embedding=_vector(r["embedding"]))
            for r in reversed(rows)
        ]  # fmt: skip

    async def known(self, tenant_id: str, employee_id: str) -> set[tuple[str, ...]]:
        """Every tool sequence that already has a skill, whatever its status."""
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                "SELECT tools FROM skills WHERE employee_id = %s", (employee_id,)
            )
            return {tuple(r["tools"]) for r in await cur.fetchall()}

    async def propose(
        self, tenant_id: str, employee_id: str, mined: MinedSkill, embedding: list[float]
    ) -> uuid.UUID | None:
        """Store the skill as proposed, with its change proposal and event. Returns the
        proposal id, or None when this tool sequence already had a skill."""
        sid = skill_id(tenant_id, employee_id, mined.tools)
        pid = uuid.uuid5(sid, "proposal")
        payload: dict[str, Any] = {"name": mined.name, "trigger": mined.trigger,
                                   "tools": list(mined.tools), "procedure": mined.procedure,
                                   "skill_id": str(sid)}  # fmt: skip
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                """
                INSERT INTO change_proposals (id, tenant_id, employee_id, kind, payload, evidence,
                    risk, proposed_by)
                VALUES (%s, %s, %s, 'skill_add', %s, %s, 'low', %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (pid, tenant_id, employee_id, Jsonb(payload),
                 Jsonb({"episodes": list(mined.evidence), "count": len(mined.evidence)}),
                 PROPOSER),
            )  # fmt: skip
            if cur.rowcount != 1:
                return None
            await conn.execute(
                """
                INSERT INTO skills (id, tenant_id, employee_id, name, trigger, tools, procedure,
                    embedding, proposal_id, evidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s::uuid[])
                """,
                (sid, tenant_id, employee_id, mined.name, mined.trigger, list(mined.tools),
                 mined.procedure, vector_literal(embedding) if embedding else None, pid,
                 list(mined.evidence)),
            )  # fmt: skip
            await enqueue(
                conn,
                new_event("brain.change_proposed", self.source, tenant_id,
                          {"proposal_id": str(pid), "kind": "skill_add", "employee_id": employee_id,
                           "text": mined.procedure, "risk": "low"},
                          subject=employee_id),
            )  # fmt: skip
        return pid

    async def by_proposal(self, tenant_id: str, proposal_id: uuid.UUID) -> Skill | None:
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute("SELECT * FROM skills WHERE proposal_id = %s", (proposal_id,))
            row = await cur.fetchone()
            return Skill.model_validate(row) if row else None

    async def list(self, tenant_id: str, employee_id: str, statuses: list[str]) -> list[Skill]:
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM skills WHERE employee_id = %s"
                " AND (cardinality(%s::text[]) = 0 OR status = ANY(%s))"
                " ORDER BY created_at DESC LIMIT 200",
                (employee_id, statuses, statuses),
            )
            return [Skill.model_validate(r) for r in await cur.fetchall()]
