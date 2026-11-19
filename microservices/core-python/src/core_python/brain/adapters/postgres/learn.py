"""Learning from outcomes in brain_db: credit assignment through `recall_log`."""

from __future__ import annotations

import uuid

from wfk_platform.db import Database

from core_python.brain.domain.learn.credit import delta
from core_python.brain.domain.write.model import Outcome


class Learning:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def credit_run(
        self, tenant_id: uuid.UUID | str, employee_id: str, run_id: str, outcome: Outcome
    ) -> int:
        """Credit or blame the active memories this run recalled. Once per run: a second call
        changes nothing and returns 0. Returns how many memories it touched."""
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                "INSERT INTO credit_log (tenant_id, run_id, outcome) VALUES (%s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (tenant_id, run_id, outcome),
            )
            if cur.rowcount != 1:
                return 0
            cur = await conn.execute(
                """
                UPDATE memories
                   SET importance = least(1.0, greatest(0.0, importance + %(d)s)),
                       use_count = use_count + 1,
                       last_used_at = now()
                 WHERE status = 'active'
                   AND id IN (SELECT DISTINCT unnest(item_ids) FROM recall_log
                               WHERE run_id = %(run)s AND employee_id = %(emp)s)
                """,
                {"d": delta(outcome), "run": run_id, "emp": employee_id},
            )
            touched = cur.rowcount
            await conn.execute(
                "UPDATE credit_log SET credited = %s WHERE run_id = %s", (touched, run_id)
            )
        return touched
