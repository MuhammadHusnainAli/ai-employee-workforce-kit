"""The Brain Inspector's reads and human controls on brain_db (brain.md section 7, Opacity).

Every change is a tenant transaction with its event in the outbox: a correction
(`brain.memory_corrected`), a deletion (`brain.memory_deleted`) or a review
(`brain.memory_reviewed`). Corrections never overwrite: they supersede, so the history stays.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, LiteralString

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from brain_service.adapters.modelgw.embeddings import Embedder
from brain_service.adapters.postgres import graph
from brain_service.adapters.postgres.memories import vector_literal
from brain_service.domain.write.governance import govern
from brain_service.domain.write.model import Candidate

COLUMNS: LiteralString = (
    "id, employee_id, kind, content, status, trust, confidence, importance, fact_key,"
    " source_ref, valid_from, valid_to, recorded_at, superseded_by, metadata, use_count"
)


class NotFound(Exception):
    pass


class Precondition(Exception):
    pass


class Inspected(BaseModel):
    id: uuid.UUID
    employee_id: str | None
    kind: str
    content: str
    status: str
    trust: str
    confidence: float
    importance: float
    fact_key: str | None
    source_ref: str | None
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    superseded_by: uuid.UUID | None
    metadata: dict[str, Any]
    use_count: int

    @property
    def findings(self) -> dict[str, int]:
        return {k: int(v) for k, v in (self.metadata.get("findings") or {}).items()}

    @property
    def contradicts(self) -> str:
        return str(self.metadata.get("contradicts") or "")

    @property
    def reinforced(self) -> int:
        return len(self.metadata.get("reinforced_by") or [])


class EpisodeRow(BaseModel):
    run_id: str | None
    task: str
    outcome: str
    ended_at: datetime | None


class Detail(BaseModel):
    memory: Inspected
    replaced: list[Inspected]
    replaced_by: Inspected | None
    episode: EpisodeRow | None


async def _one(conn: Conn, memory_id: uuid.UUID, *, lock: bool = False) -> Inspected:
    sql: LiteralString = f"SELECT {COLUMNS} FROM memories WHERE id = %s"  # noqa: S608
    cur = await conn.execute(sql + (" FOR UPDATE" if lock else ""), (memory_id,))
    row = await cur.fetchone()
    if row is None:
        raise NotFound(f"no memory {memory_id}")
    return Inspected.model_validate(row)


class Inspector:
    def __init__(self, db: Database, embedder: Embedder, *, service_source: str) -> None:
        self.db = db
        self.embedder = embedder
        self.source = service_source

    async def list(
        self,
        tenant_id: str,
        employee_id: str,
        *,
        kinds: list[str],
        statuses: list[str],
        include_history: bool,
        limit: int,
        before: datetime | None,
    ) -> list[Inspected]:
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM memories"  # noqa: S608 - constant columns
                " WHERE employee_id = %(emp)s"
                "   AND (cardinality(%(kinds)s::text[]) = 0 OR kind = ANY(%(kinds)s))"
                "   AND (cardinality(%(statuses)s::text[]) = 0 OR status = ANY(%(statuses)s))"
                "   AND (%(history)s OR valid_to IS NULL)"
                "   AND (%(before)s::timestamptz IS NULL OR recorded_at < %(before)s)"
                " ORDER BY recorded_at DESC LIMIT %(limit)s",
                {"emp": employee_id, "kinds": kinds, "statuses": statuses,
                 "history": include_history, "before": before, "limit": limit},
            )  # fmt: skip
            return [Inspected.model_validate(r) for r in await cur.fetchall()]

    async def get(self, tenant_id: str, memory_id: uuid.UUID) -> Detail:
        async with self.db.tenant_tx(tenant_id) as conn:
            memory = await _one(conn, memory_id)
            cur = await conn.execute(
                f"""
                WITH RECURSIVE older AS (
                    SELECT {COLUMNS}, 1 AS depth FROM memories WHERE superseded_by = %s
                    UNION ALL
                    SELECT m.id, m.employee_id, m.kind, m.content, m.status, m.trust,
                           m.confidence, m.importance, m.fact_key, m.source_ref, m.valid_from,
                           m.valid_to, m.recorded_at, m.superseded_by, m.metadata, m.use_count,
                           o.depth + 1
                      FROM memories m JOIN older o ON m.superseded_by = o.id
                     WHERE o.depth < 50
                )
                SELECT {COLUMNS} FROM older ORDER BY depth
                """,  # noqa: S608 - constant columns
                (memory_id,),
            )
            replaced = [Inspected.model_validate(r) for r in await cur.fetchall()]
            replaced_by = await _one(conn, memory.superseded_by) if memory.superseded_by else None
            cur = await conn.execute(
                "SELECT run_id, task, outcome, ended_at FROM episodes WHERE memory_id = %s",
                (memory_id,),
            )
            ep = await cur.fetchone()
        return Detail(
            memory=memory,
            replaced=replaced,
            replaced_by=replaced_by,
            episode=EpisodeRow.model_validate(ep) if ep else None,
        )

    async def correct(
        self, tenant_id: str, memory_id: uuid.UUID, content: str, *, by: str, key: str
    ) -> Inspected:
        new_id = uuid.uuid5(memory_id, f"correction/{key}")
        async with self.db.tenant_tx(tenant_id) as conn:
            cur = await conn.execute("SELECT 1 FROM memories WHERE id = %s", (new_id,))
            if await cur.fetchone() is not None:
                return await _one(conn, new_id)  # retried with the same key
            old = await _one(conn, memory_id, lock=True)
            if old.valid_to is not None or old.status != "active":
                raise Precondition("only a current, active memory can be corrected")
        # A person wrote it: human-verified. Secrets are still removed; PII is kept, since a
        # person chose to write it.
        g = govern(
            Candidate(kind=old.kind, content=content, origin="inspector",  # type: ignore[arg-type]
                      confidence=1.0, importance=old.importance, key=old.fact_key),
            "keep",
        )  # fmt: skip
        if g is None:
            raise Precondition("the correction is empty after governance")
        (vec,) = await self.embedder.embed([g.content])
        async with self.db.tenant_tx(tenant_id) as conn:
            old = await _one(conn, memory_id, lock=True)
            if old.valid_to is not None:
                raise Precondition("the memory changed meanwhile; reload and retry")
            await conn.execute(
                """
                INSERT INTO memories (id, tenant_id, employee_id, scope, kind, content, embedding,
                    importance, confidence, trust, source_ref, status, fact_key, metadata)
                SELECT %s, tenant_id, employee_id, scope, kind, %s, %s::vector, importance, 1.0,
                       'human_verified', %s, 'active', fact_key, %s
                  FROM memories WHERE id = %s
                """,
                (new_id, g.content, vector_literal(vec), f"inspector:{by}",
                 Jsonb({"corrects": str(memory_id)} | ({"findings": g.findings}
                       if g.findings else {})), memory_id),
            )  # fmt: skip
            await conn.execute(
                "UPDATE memories SET superseded_by = %s,"
                " valid_to = greatest(now(), valid_from + interval '1 microsecond')"
                " WHERE id = %s",
                (new_id, memory_id),
            )
            # Still about the same entities; what the old text stated no longer holds.
            await conn.execute(
                "INSERT INTO memory_entities (memory_id, entity_id, tenant_id)"
                " SELECT %s, entity_id, tenant_id FROM memory_entities WHERE memory_id = %s",
                (new_id, memory_id),
            )
            await graph.close_relations_of(conn, memory_id)
            await self._event(
                conn,
                tenant_id,
                "brain.memory_corrected",
                old.employee_id,
                {"memory_id": str(new_id), "corrects": str(memory_id), "by": by},
            )
            return await _one(conn, new_id)  # fmt: skip

    async def delete(self, tenant_id: str, memory_id: uuid.UUID, *, by: str, reason: str) -> None:
        async with self.db.tenant_tx(tenant_id) as conn:
            old = await _one(conn, memory_id, lock=True)
            # Versions that pointed at it keep their history but no longer point anywhere.
            await conn.execute(
                "UPDATE memories SET superseded_by = NULL WHERE superseded_by = %s", (memory_id,)
            )
            await conn.execute("DELETE FROM memories WHERE id = %s", (memory_id,))
            data = {"memory_id": str(memory_id), "by": by, "reason": reason}
            await self._event(conn, tenant_id, "brain.memory_deleted", old.employee_id, data)

    async def review(
        self, tenant_id: str, memory_id: uuid.UUID, *, approve: bool, by: str, note: str
    ) -> Inspected:
        async with self.db.tenant_tx(tenant_id) as conn:
            m = await _one(conn, memory_id, lock=True)
            if m.status != "quarantined":
                raise Precondition(f"memory is {m.status}, not quarantined")
            meta = m.metadata | {"reviewed_by": by, "review_note": note}
            await conn.execute(
                "UPDATE memories SET status = %s, metadata = %s WHERE id = %s",
                ("active" if approve else "rejected", Jsonb(meta), memory_id),
            )
            if approve and m.contradicts:
                # The person sided with the newer memory: it replaces the one it contradicted.
                await conn.execute(
                    "UPDATE memories SET superseded_by = %s,"
                    " valid_to = greatest(now(), valid_from + interval '1 microsecond')"
                    " WHERE id = %s AND valid_to IS NULL",
                    (memory_id, uuid.UUID(m.contradicts)),
                )
                await graph.close_relations_of(conn, uuid.UUID(m.contradicts))
            data = {"memory_id": str(memory_id), "approved": approve, "by": by}
            await self._event(conn, tenant_id, "brain.memory_reviewed", m.employee_id, data)
            return await _one(conn, memory_id)

    async def _event(
        self, conn: Conn, tenant_id: str, type_: str, employee_id: str | None,
        data: dict[str, Any],
    ) -> None:  # fmt: skip
        await enqueue(
            conn,
            new_event(type_, self.source, tenant_id, data | {"employee_id": employee_id},
                      subject=employee_id),
        )  # fmt: skip
