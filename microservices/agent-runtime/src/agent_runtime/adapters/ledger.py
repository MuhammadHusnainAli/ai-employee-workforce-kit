"""The run ledger in run_db: `runs` and `run_steps`, with their events in the outbox.

Each write is one tenant-scoped transaction: the ledger rows and the outbox events commit
together or not at all (principle 3). Writes are idempotent: an entry's (run_id, seq) is its
primary key, so a retry after a crash finds the row and writes nothing, events included.
"""

from __future__ import annotations

from psycopg.types.json import Jsonb

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from agent_runtime.workflows.types import LedgerEntry, StartRecord

TERMINAL = {"completed": "run.completed", "failed": "run.failed"}
#: Task and output carried on run.completed / run.failed, so events stay small.
EVENT_TEXT_LIMIT = 8000


def _clip(text: str) -> str:
    return text if len(text) <= EVENT_TEXT_LIMIT else text[: EVENT_TEXT_LIMIT - 3] + "..."


class Ledger:
    def __init__(self, db: Database, source: str) -> None:
        self.db = db
        self.source = source  # CloudEvent source, e.g. //cell-eu1/agent-runtime

    async def start(self, rec: StartRecord) -> bool:
        """Create the run row and emit run.started. Returns False if it already existed."""
        async with self.db.tenant_tx(rec.tenant_id) as conn:
            cur = await conn.execute(
                """
                INSERT INTO runs (run_id, tenant_id, employee_id, task, status, mode, candidate)
                VALUES (%s, %s, %s, %s, 'running', %s, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    rec.run_id,
                    rec.tenant_id,
                    rec.employee_id,
                    rec.task,
                    rec.mode,
                    rec.candidate,
                ),
            )
            if cur.rowcount != 1:
                return False
            await enqueue(
                conn,
                new_event(
                    "run.started",
                    self.source,
                    rec.tenant_id,
                    {
                        "run_id": rec.run_id,
                        "employee_id": rec.employee_id,
                        "mode": rec.mode,
                        "candidate": rec.candidate,
                    },
                    subject=rec.run_id,
                ),
            )
        return True

    async def record(self, e: LedgerEntry) -> bool:
        """Write one entry, update the run, emit its events. Returns False on a repeat."""
        async with self.db.tenant_tx(e.tenant_id) as conn:
            cur = await conn.execute(
                """
                INSERT INTO run_steps (run_id, seq, tenant_id, step, kind, decision, results,
                    model, prompt_hash, input_tokens, output_tokens, cost_usd, status_after,
                    recall_id, memory_degraded)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, seq) DO NOTHING
                """,
                (
                    e.run_id, e.seq, e.tenant_id, e.step, e.kind, Jsonb(e.decision),
                    Jsonb(e.results), e.model, e.prompt_hash, e.input_tokens, e.output_tokens,
                    e.cost_usd, e.status, e.recall_id, e.memory_degraded,
                ),
            )  # fmt: skip
            if cur.rowcount != 1:
                return False
            await self._update_run(conn, e)
            data = {
                "run_id": e.run_id,
                "employee_id": e.employee_id,
                "seq": e.seq,
                "step": e.step,
                "kind": e.kind,
                "status": e.status,
                "mode": e.mode,
            }
            await enqueue(
                conn,
                new_event("run.step_completed", self.source, e.tenant_id, data, subject=e.run_id),
            )
            if e.status in TERMINAL:
                # The task and the output go with the terminal event: brain-ingest remembers
                # the run from it without reading this service's tables.
                cur = await conn.execute("SELECT task FROM runs WHERE run_id = %s", (e.run_id,))
                row = await cur.fetchone()
                terminal = {
                    **data,
                    "stop_reason": e.stop_reason,
                    "cost_usd": e.total_cost_usd,
                    "task": _clip(row["task"] if row else ""),
                    "output": _clip(e.output or ""),
                }
                await enqueue(
                    conn,
                    new_event(TERMINAL[e.status], self.source, e.tenant_id, terminal,
                              subject=e.run_id),
                )  # fmt: skip
        return True

    @staticmethod
    async def _update_run(conn: Conn, e: LedgerEntry) -> None:
        await conn.execute(
            """
            UPDATE runs SET status = %s, stop_reason = %s, output = %s, steps = %s,
                input_tokens = %s, output_tokens = %s, cost_usd = %s, updated_at = now()
            WHERE run_id = %s
            """,
            (
                e.status, e.stop_reason, e.output, e.step, e.total_input_tokens,
                e.total_output_tokens, e.total_cost_usd, e.run_id,
            ),
        )  # fmt: skip
