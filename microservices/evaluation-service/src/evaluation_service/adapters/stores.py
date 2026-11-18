"""eval_db (evaluations and their events), and the employee's past successes from the brain."""

from __future__ import annotations

import uuid
from typing import Any

import grpc
from psycopg.types.json import Jsonb

from wfk.brain_service.v1 import brain_pb2, brain_pb2_grpc
from wfk.common.v1 import common_pb2
from wfk_platform.db import Database
from wfk_platform.outbox import enqueue, new_event


def eval_id(tenant_id: str, proposal_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.UUID(tenant_id), f"eval/{proposal_id}")


class Evals:
    def __init__(self, db: Database, *, service_source: str) -> None:
        self.db = db
        self.source = service_source

    async def start(self, tenant_id: str, employee_id: str, proposal_id: str) -> uuid.UUID:
        eid = eval_id(tenant_id, proposal_id)
        async with self.db.tenant_tx(tenant_id) as conn:
            await conn.execute(
                "INSERT INTO eval_runs (id, tenant_id, employee_id, proposal_id)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (eid, tenant_id, employee_id, proposal_id),
            )
        return eid

    async def finish(
        self,
        tenant_id: str,
        employee_id: str,
        proposal_id: str,
        *,
        passed: bool,
        reasons: list[str],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        comparison: dict[str, Any],
    ) -> None:
        """Store the verdict with eval.completed (and eval.regression_detected), once."""
        eid = eval_id(tenant_id, proposal_id)
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                """
                UPDATE eval_runs SET status = %s, reasons = %s, baseline = %s, candidate = %s,
                    comparison = %s, finished_at = now()
                 WHERE id = %s AND status = 'running'
                """,
                ("passed" if passed else "failed", reasons, Jsonb(baseline), Jsonb(candidate),
                 Jsonb(comparison), eid),
            )  # fmt: skip
            if cur.rowcount != 1:
                return  # already finished: a retried step adds nothing
            data = {
                "eval_id": str(eid), "proposal_id": proposal_id, "employee_id": employee_id,
                "passed": passed, "reasons": reasons,
                "baseline_score": comparison.get("baseline_score"),
                "candidate_score": comparison.get("candidate_score"),
                "regressed": comparison.get("regressed", []),
            }  # fmt: skip
            await enqueue(conn, new_event("eval.completed", self.source, tenant_id, data,
                                          subject=proposal_id))  # fmt: skip
            if comparison.get("regressed"):
                await enqueue(conn, new_event("eval.regression_detected", self.source,
                                              tenant_id, data, subject=proposal_id))  # fmt: skip

    async def get(self, tenant_id: str, proposal_id: str) -> dict[str, Any] | None:
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                "SELECT * FROM eval_runs WHERE id = %s", (eval_id(tenant_id, proposal_id),)
            )
            row = await cur.fetchone()
        return dict(row) if row else None


class BrainEpisodes:
    """The employee's episodic memories, for its regression set."""

    def __init__(self, address: str, *, deadline_s: float = 5.0) -> None:
        self.channel = grpc.aio.insecure_channel(address)
        self.stub = brain_pb2_grpc.BrainServiceStub(self.channel)
        self.deadline_s = deadline_s

    async def successes(self, tenant_id: str, employee_id: str, limit: int = 50) -> list[str]:
        resp = await self.stub.ListMemories(
            brain_pb2.ListMemoriesRequest(
                tenant=common_pb2.TenantContext(tenant_id=tenant_id),
                employee_id=employee_id,
                kinds=["episodic"],
                statuses=[brain_pb2.MEMORY_STATUS_ACTIVE],
                page=common_pb2.PageRequest(page_size=limit),
            ),
            timeout=self.deadline_s,
        )
        return [m.content for m in resp.memories if "Outcome: success" in m.content]
