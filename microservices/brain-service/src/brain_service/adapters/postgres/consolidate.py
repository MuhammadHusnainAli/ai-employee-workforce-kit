"""Consolidation's reads and writes in brain_db. Every step is idempotent, so a pass that
crashes resumes (Temporal retries the step) without doing anything twice."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from wfk_platform.db import Database

from brain_service.domain.learn.consolidate import DECAY, FLOOR, UNUSED_DAYS, Episode

MAX_EPISODES = 200  # per pass: the budget that keeps a pass short


class Batch(BaseModel):
    tenant_id: str
    employee_id: str
    #: The newest episode read: the watermark once this pass is done.
    until: datetime | None
    #: When the pass began: due markers older than this are cleared when it finishes.
    started: datetime
    episodes: list[dict[str, Any]]


def _vector(text: str | None) -> list[float]:
    if not text:
        return []
    return [float(x) for x in text.strip("[]").split(",") if x]


class Consolidation:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def due(self) -> list[tuple[str, str]]:
        """Employees with new episodes, across tenants (ids only)."""
        async with self.db.tx() as conn:
            cur = await conn.execute(
                "SELECT tenant_id, employee_id FROM consolidation_due ORDER BY since LIMIT 1000"
            )
            return [(str(r["tenant_id"]), r["employee_id"]) for r in await cur.fetchall()]

    async def select(self, tenant_id: str, employee_id: str) -> Batch:
        """Episodes after the watermark, oldest first, within the budget."""
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute("SELECT now() AS started")
            now = await cur.fetchone()
            assert now is not None
            started: datetime = now["started"]
            cur = await conn.execute(
                "SELECT watermark FROM consolidations WHERE employee_id = %s", (employee_id,)
            )
            row = await cur.fetchone()
            watermark = row["watermark"] if row else None
            cur = await conn.execute(
                """
                SELECT m.id, e.task, e.outcome, m.recorded_at, m.embedding::text AS embedding
                  FROM episodes e JOIN memories m ON m.id = e.memory_id
                 WHERE e.employee_id = %s AND m.status = 'active'
                   AND (%s::timestamptz IS NULL OR m.recorded_at > %s)
                 ORDER BY m.recorded_at LIMIT %s
                """,
                (employee_id, watermark, watermark, MAX_EPISODES),
            )
            rows = await cur.fetchall()
        return Batch(
            tenant_id=tenant_id,
            employee_id=employee_id,
            until=rows[-1]["recorded_at"] if rows else None,
            started=started,
            episodes=[
                {"memory_id": str(r["id"]), "task": r["task"], "outcome": r["outcome"],
                 "embedding": _vector(r["embedding"])}
                for r in rows
            ],
        )  # fmt: skip

    @staticmethod
    def episodes(batch: Batch) -> list[Episode]:
        return [Episode(**e) for e in batch.episodes]

    async def decay(self, tenant_id: str, employee_id: str) -> dict[str, int]:
        """One pass of disuse: memories not used for UNUSED_DAYS lose importance; below the
        floor they are archived. A person's confirmed facts never decay."""
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                """
                UPDATE memories SET importance = round((importance * %(decay)s)::numeric, 6)
                 WHERE employee_id = %(emp)s AND status = 'active' AND valid_to IS NULL
                   AND trust <> 'human_verified'
                   AND coalesce(last_used_at, recorded_at) < now() - make_interval(days => %(days)s)
                """,
                {"decay": DECAY, "emp": employee_id, "days": UNUSED_DAYS},
            )
            decayed = cur.rowcount
            cur = await conn.execute(
                "UPDATE memories SET status = 'archived' WHERE employee_id = %s"
                " AND status = 'active' AND importance < %s AND trust <> 'human_verified'",
                (employee_id, FLOOR),
            )
            return {"decayed": decayed, "archived": cur.rowcount}

    async def merge_entities(self, tenant_id: str, employee_id: str) -> int:
        """Entities of one type where one's name is the other's alias are one entity: keep the
        older, move links and relations to it, learn the aliases, delete the duplicate."""
        merged = 0
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                """
                SELECT keep.id AS keep, dup.id AS dup, dup.norm_name, dup.aliases
                  FROM entities keep JOIN entities dup
                    ON keep.employee_id IS NOT DISTINCT FROM dup.employee_id
                   AND keep.type = dup.type AND keep.id <> dup.id
                   AND (dup.norm_name = ANY(keep.aliases) OR keep.norm_name = ANY(dup.aliases))
                   AND (keep.created_at, keep.id) < (dup.created_at, dup.id)
                 WHERE keep.employee_id = %s
                """,
                (employee_id,),
            )
            pairs = await cur.fetchall()
            gone: set[uuid.UUID] = set()
            for p in pairs:
                keep, dup = p["keep"], p["dup"]
                if keep in gone or dup in gone:
                    continue
                await conn.execute(
                    "INSERT INTO memory_entities (memory_id, entity_id, tenant_id)"
                    " SELECT memory_id, %s, tenant_id FROM memory_entities WHERE entity_id = %s"
                    " ON CONFLICT DO NOTHING",
                    (keep, dup),
                )
                # Relations between the two would become self-loops (forbidden): drop them first.
                await conn.execute(
                    "DELETE FROM relations WHERE (src_id = %(k)s AND dst_id = %(d)s)"
                    " OR (src_id = %(d)s AND dst_id = %(k)s)",
                    {"k": keep, "d": dup},
                )
                await conn.execute(
                    "UPDATE relations SET src_id = %s WHERE src_id = %s", (keep, dup)
                )
                await conn.execute(
                    "UPDATE relations SET dst_id = %s WHERE dst_id = %s", (keep, dup)
                )
                await conn.execute(
                    "UPDATE entities SET aliases = ARRAY(SELECT DISTINCT a FROM unnest("
                    " aliases || %s::text[] || ARRAY[%s]) a WHERE a <> norm_name)"
                    " WHERE id = %s",
                    (p["aliases"], p["norm_name"], keep),
                )
                await conn.execute("DELETE FROM entities WHERE id = %s", (dup,))
                gone.add(dup)
                merged += 1
        return merged

    async def finish(self, batch: Batch, counts: dict[str, int]) -> None:
        """Advance the watermark, and clear the due marker unless a newer episode set it again
        during the pass (or the budget left episodes for the next pass)."""
        tenant_id, employee_id, until = batch.tenant_id, batch.employee_id, batch.until
        if len(batch.episodes) < MAX_EPISODES:
            async with self.db.tx() as conn:
                await conn.execute(
                    "DELETE FROM consolidation_due WHERE tenant_id = %s AND employee_id = %s"
                    " AND since <= %s",
                    (tenant_id, employee_id, batch.started),
                )
        async with self.db.tenant_tx(tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO consolidations (tenant_id, employee_id, watermark, last_run_at, counts)
                VALUES (%(t)s, %(e)s, coalesce(%(u)s, '-infinity'::timestamptz), now(), %(c)s)
                ON CONFLICT (tenant_id, employee_id) DO UPDATE
                   SET watermark = greatest(consolidations.watermark, excluded.watermark),
                       last_run_at = now(), counts = excluded.counts
                """,
                {"t": tenant_id, "e": employee_id, "u": until, "c": Jsonb(counts)},
            )
