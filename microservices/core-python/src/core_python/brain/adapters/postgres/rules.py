"""Rule proposals, decisions and active rules in brain_db (brain.md section 6, steps 2 to 4).

A rule only becomes active when a person approves its proposal. A high-risk rule needs a second
person: whoever proposed it cannot approve it. Every decision and revocation is one
transaction with its event (`brain.rule_activated`, `brain.change_rejected`,
`brain.rule_revoked`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from core_python.brain.adapters.postgres import skills as skills_repo
from core_python.brain.adapters.postgres.inspect import NotFound, Precondition


class Proposal(BaseModel):
    id: uuid.UUID
    employee_id: str
    kind: str
    payload: dict[str, Any]
    evidence: dict[str, Any]
    risk: str
    status: str
    proposed_by: str
    decided_by: str | None
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None
    eval_status: str = "pending"
    eval_reasons: list[str] = []


class Rule(BaseModel):
    id: uuid.UUID
    employee_id: str | None
    text: str
    status: str
    proposal_id: uuid.UUID | None
    approved_by: str
    created_at: datetime
    revoked_by: str | None
    revoked_at: datetime | None


async def active_rules(conn: Conn, employee_id: str) -> list[str]:
    """The rules pinned in this employee's every recall: its own and the tenant's, oldest
    first so the list is stable."""
    cur = await conn.execute(
        "SELECT text FROM policy_rules WHERE status = 'active'"
        " AND (employee_id = %s OR employee_id IS NULL) ORDER BY created_at, id",
        (employee_id,),
    )
    return [r["text"] for r in await cur.fetchall()]


async def candidate_rule(conn: Conn, employee_id: str, proposal_id: str) -> str | None:
    """A proposed (not yet active) rule's text, for evaluation and shadow runs of this
    employee. None for anything else: unknown, rejected, another employee's, not a UUID."""
    try:
        uuid.UUID(proposal_id)
    except ValueError:
        return None
    cur = await conn.execute(
        "SELECT payload->>'text' AS text FROM change_proposals WHERE id = %s"
        " AND employee_id = %s AND kind = 'rule_add' AND status <> 'rejected'",
        (proposal_id, employee_id),
    )
    row = await cur.fetchone()
    return row["text"] if row else None


async def record_evaluation(conn: Conn, data: dict[str, Any]) -> bool:
    """An eval.completed verdict for a pending proposal. Later verdicts for a decided proposal
    change nothing. Returns whether a proposal was updated."""
    try:
        pid = uuid.UUID(str(data.get("proposal_id")))
    except ValueError:
        return False
    cur = await conn.execute(
        "UPDATE change_proposals SET eval_status = %s, eval_reasons = %s, evaluated_at = now()"
        " WHERE id = %s AND status = 'pending'",
        ("passed" if data.get("passed") is True else "failed",
         [str(r) for r in data.get("reasons") or []], pid),
    )  # fmt: skip
    return cur.rowcount == 1


class Rules:
    def __init__(self, db: Database, *, service_source: str) -> None:
        self.db = db
        self.source = service_source

    async def proposals(
        self, tenant_id: str, employee_id: str, statuses: list[str]
    ) -> list[Proposal]:
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM change_proposals WHERE employee_id = %s"
                " AND (cardinality(%s::text[]) = 0 OR status = ANY(%s))"
                " ORDER BY created_at DESC LIMIT 200",
                (employee_id, statuses, statuses),
            )
            return [Proposal.model_validate(r) for r in await cur.fetchall()]

    async def decide(
        self, tenant_id: str, proposal_id: uuid.UUID, *, approve: bool, by: str, note: str
    ) -> tuple[Proposal, Rule | None]:
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM change_proposals WHERE id = %s FOR UPDATE", (proposal_id,)
            )
            row = await cur.fetchone()
            if row is None:
                raise NotFound(f"no proposal {proposal_id}")
            p = Proposal.model_validate(row)
            if p.status != "pending":
                raise Precondition(f"proposal is already {p.status}")
            if approve and p.eval_status != "passed":
                # Fail closed: no passing evaluation, no approval (brain.md section 6, step 3).
                raise Precondition(
                    "the evaluation has not finished yet"
                    if p.eval_status == "pending"
                    else "the evaluation failed: " + "; ".join(p.eval_reasons)
                )
            if approve and p.risk == "high" and by == p.proposed_by:
                raise Precondition("a high-risk change needs a second person to approve it")
            await conn.execute(
                "UPDATE change_proposals SET status = %s, decided_by = %s, decision_note = %s,"
                " decided_at = now() WHERE id = %s",
                ("approved" if approve else "rejected", by, note, proposal_id),
            )
            rule = None
            if p.kind == "skill_add":
                skill = await skills_repo.decide(conn, proposal_id, approve=approve, by=by)
                if approve and skill is not None:
                    data = {"skill_id": str(skill.id), "proposal_id": str(proposal_id),
                            "name": skill.name, "tools": skill.tools, "by": by}  # fmt: skip
                    await self._event(conn, tenant_id, "brain.skill_activated", p.employee_id, data)
                if not approve:
                    data = {"proposal_id": str(proposal_id), "by": by, "note": note}
                    await self._event(conn, tenant_id, "brain.change_rejected", p.employee_id, data)
            elif approve:
                rid = uuid.uuid5(proposal_id, "rule")
                await conn.execute(
                    "INSERT INTO policy_rules (id, tenant_id, employee_id, text, proposal_id,"
                    " approved_by) VALUES (%s, %s, %s, %s, %s, %s)",
                    (rid, tenant_id, p.employee_id, p.payload["text"], proposal_id, by),
                )
                rule = await self._rule(conn, rid)
                await self._event(conn, tenant_id, "brain.rule_activated", p.employee_id,
                                  {"rule_id": str(rid), "proposal_id": str(proposal_id),
                                   "text": rule.text, "by": by})  # fmt: skip
            else:
                data = {"proposal_id": str(proposal_id), "by": by, "note": note}
                await self._event(conn, tenant_id, "brain.change_rejected", p.employee_id, data)
            cur = await conn.execute("SELECT * FROM change_proposals WHERE id = %s", (proposal_id,))
            decided = Proposal.model_validate(await cur.fetchone())
        return decided, rule

    async def rules(self, tenant_id: str, employee_id: str, *, include_revoked: bool) -> list[Rule]:
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM policy_rules WHERE (employee_id = %s OR employee_id IS NULL)"
                " AND (%s OR status = 'active') ORDER BY created_at, id",
                (employee_id, include_revoked),
            )
            return [Rule.model_validate(r) for r in await cur.fetchall()]

    async def revoke(self, tenant_id: str, rule_id: uuid.UUID, *, by: str, reason: str) -> Rule:
        async with self.db.tenant_tx(tenant_id) as conn:
            rule = await self._rule(conn, rule_id, lock=True)
            if rule.status != "active":
                raise Precondition("the rule is already revoked")
            await conn.execute(
                "UPDATE policy_rules SET status = 'revoked', revoked_by = %s, revoked_at = now()"
                " WHERE id = %s",
                (by, rule_id),
            )
            await self._event(conn, tenant_id, "brain.rule_revoked", rule.employee_id,
                              {"rule_id": str(rule_id), "by": by, "reason": reason})  # fmt: skip
            return await self._rule(conn, rule_id)

    async def _rule(self, conn: Conn, rule_id: uuid.UUID, *, lock: bool = False) -> Rule:
        if lock:
            cur = await conn.execute(
                "SELECT * FROM policy_rules WHERE id = %s FOR UPDATE", (rule_id,)
            )
        else:
            cur = await conn.execute("SELECT * FROM policy_rules WHERE id = %s", (rule_id,))
        row = await cur.fetchone()
        if row is None:
            raise NotFound(f"no rule {rule_id}")
        return Rule.model_validate(row)

    async def _event(self, conn: Conn, tenant_id: str, type_: str, employee_id: str | None,
                     data: dict[str, Any]) -> None:  # fmt: skip
        await enqueue(
            conn,
            new_event(type_, self.source, tenant_id, data | {"employee_id": employee_id},
                      subject=employee_id),
        )  # fmt: skip
