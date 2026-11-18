"""The commit (brain.md section 4, step 5): memories, episodes, supersessions and the
`brain.memory_committed` event, in one tenant transaction.

Idempotent on the source event: `ingest_log` has one row per event, inserted first. A second
commit of the same event finds it and returns what the first one stored, writing nothing.
Memory ids are derived from the event id too, so even the ids are stable across retries.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from brain_service.adapters.postgres.memories import vector_literal
from brain_service.domain.write.model import Committed, Governed, PiiPolicy, Source
from brain_service.domain.write.resolve import Existing, resolve

EVENT = "brain.memory_committed"


def memory_id(source: Source, index: int) -> uuid.UUID:
    return uuid.uuid5(source.event_id, f"memory/{index}")


async def pii_policy(db: Database, tenant_id: uuid.UUID, default: PiiPolicy) -> PiiPolicy:
    async with db.tenant_tx(tenant_id) as conn:
        cur = await conn.execute("SELECT pii_policy FROM tenant_policies")
        row = await cur.fetchone()
    return row["pii_policy"] if row else default


async def _nearby(conn: Conn, source: Source, g: Governed, emb: str) -> list[Existing]:
    """Current memories that could clash: the nearest of the same kind, and same-key facts."""
    cur = await conn.execute(
        """
        (SELECT id, kind, content, trust, confidence, fact_key, embedding <=> %(e)s::vector AS d
           FROM memories
          WHERE employee_id = %(emp)s AND kind = %(kind)s AND status = 'active'
            AND valid_to IS NULL
          ORDER BY embedding <=> %(e)s::vector LIMIT 3)
        UNION
        (SELECT id, kind, content, trust, confidence, fact_key, embedding <=> %(e)s::vector AS d
           FROM memories
          WHERE employee_id = %(emp)s AND fact_key = %(key)s AND status = 'active'
            AND valid_to IS NULL)
        """,
        {"e": emb, "emp": source.employee_id, "kind": g.candidate.kind, "key": g.candidate.key},
    )
    return [
        Existing(r["id"], r["kind"], r["content"], r["trust"], r["confidence"], r["fact_key"],
                 float(r["d"]) if r["d"] is not None else 2.0)
        for r in await cur.fetchall()
    ]  # fmt: skip


async def _insert(
    conn: Conn, mid: uuid.UUID, source: Source, g: Governed, emb: str, status: str,
    metadata: dict[str, Any],
) -> None:  # fmt: skip
    c = g.candidate
    await conn.execute(
        """
        INSERT INTO memories (id, tenant_id, employee_id, scope, kind, content, embedding,
            importance, confidence, trust, source_ref, status, fact_key, metadata)
        VALUES (%s, %s, %s, 'employee', %s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s)
        """,
        (mid, source.tenant_id, source.employee_id, c.kind, g.content, emb, c.importance,
         c.confidence, g.trust, f"event:{source.event_id}", status, c.key, Jsonb(metadata)),
    )  # fmt: skip
    if c.kind == "episodic":
        await conn.execute(
            """
            INSERT INTO episodes (id, tenant_id, employee_id, memory_id, run_id, task, outcome,
                ended_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (uuid.uuid5(mid, "episode"), source.tenant_id, source.employee_id, mid,
             source.run_id, source.task[:4000], source.outcome, source.occurred_at),
        )  # fmt: skip


async def commit(
    db: Database,
    source: Source,
    governed: list[Governed],
    embeddings: list[list[float]],
    *,
    service_source: str,
    dropped: int = 0,
) -> Committed:
    """Resolve and store everything learned from one source, in one transaction."""
    if len(governed) != len(embeddings):
        raise ValueError("one embedding per governed candidate")
    async with db.tenant_tx(source.tenant_id) as conn:
        cur = await conn.execute(
            """
            INSERT INTO ingest_log (tenant_id, source_event_id, event_type, employee_id)
            VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (source.tenant_id, source.event_id, source.event_type, source.employee_id),
        )
        if cur.rowcount != 1:
            cur = await conn.execute(
                "SELECT memory_ids, counts FROM ingest_log WHERE source_event_id = %s",
                (source.event_id,),
            )
            row = await cur.fetchone()
            assert row is not None
            return Committed(
                source_event_id=source.event_id, memory_ids=row["memory_ids"],
                counts=row["counts"], already=True,
            )  # fmt: skip

        counts = {"inserted": 0, "reinforced": 0, "superseded": 0, "flagged": 0,
                  "quarantined": 0, "dropped": dropped}  # fmt: skip
        stored: list[uuid.UUID] = []
        for i, (g, vec) in enumerate(zip(governed, embeddings, strict=True)):
            emb = vector_literal(vec)
            decision = resolve(g, await _nearby(conn, source, g, emb))
            metadata: dict[str, Any] = {"findings": g.findings} if g.findings else {}
            mid = memory_id(source, i)
            if decision.action == "reinforce":
                await conn.execute(
                    """
                    UPDATE memories SET confidence = greatest(confidence, %s),
                        importance = least(1.0, importance + 0.05),
                        metadata = jsonb_set(metadata, '{reinforced_by}',
                            coalesce(metadata->'reinforced_by', '[]'::jsonb) || %s)
                    WHERE id = %s
                    """,
                    (g.candidate.confidence, Jsonb([str(source.event_id)]), decision.target),
                )
                counts["reinforced"] += 1
                continue
            status = g.status
            if decision.action == "flag":
                status = "quarantined"
                metadata["contradicts"] = str(decision.target)
                counts["flagged"] += 1
            await _insert(conn, mid, source, g, emb, status, metadata)
            if decision.action == "supersede":
                await conn.execute(
                    "UPDATE memories SET superseded_by = %s,"
                    " valid_to = greatest(now(), valid_from + interval '1 microsecond')"
                    " WHERE id = %s",
                    (mid, decision.target),
                )
                counts["superseded"] += 1
            counts["inserted"] += 1
            if status == "quarantined":
                counts["quarantined"] += 1
            stored.append(mid)

        await conn.execute(
            "UPDATE ingest_log SET memory_ids = %s, counts = %s WHERE source_event_id = %s",
            (stored, Jsonb(counts), source.event_id),
        )
        await enqueue(
            conn,
            new_event(
                EVENT,
                service_source,
                str(source.tenant_id),
                {
                    "source_event_id": str(source.event_id),
                    "employee_id": source.employee_id,
                    "run_id": source.run_id,
                    "memory_ids": [str(m) for m in stored],
                    "counts": counts,
                },
                subject=source.employee_id,
            ),
        )
    return Committed(source_event_id=source.event_id, memory_ids=stored, counts=counts)
