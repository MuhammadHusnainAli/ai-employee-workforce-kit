"""The semantic graph in brain_db: entity resolution, relations and memory links.

Entity resolution (brain.md section 4, step 4), in order:
1. the normalised name, or one of the entity's aliases, matches;
2. otherwise the nearest entity of the same type by name embedding, if close enough;
3. otherwise a new entity.
A match learns the new spelling as an alias. Everything runs in the caller's transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from wfk_platform.db import Conn

from brain_service.adapters.postgres.memories import vector_literal
from brain_service.domain.graph.model import EntityRef

#: Cosine distance under which two names of the same type are one entity.
SAME_ENTITY_DISTANCE = 0.12


@dataclass(frozen=True)
class Resolved:
    id: uuid.UUID
    created: bool


async def resolve_entity(
    conn: Conn,
    tenant_id: uuid.UUID | str,
    employee_id: str | None,
    ref: EntityRef,
    embedding: list[float],
) -> Resolved:
    norm = ref.norm
    aliases = ref.norm_aliases()
    names = [norm, *aliases]
    # 1. by name or alias, in this employee's graph or the tenant's
    cur = await conn.execute(
        """
        SELECT id FROM entities
         WHERE (employee_id = %(emp)s OR employee_id IS NULL)
           AND (norm_name = ANY(%(names)s) OR aliases && %(names)s)
         ORDER BY (employee_id IS NULL), (norm_name = %(norm)s) DESC
         LIMIT 1
        """,
        {"emp": employee_id, "names": names, "norm": norm},
    )
    row = await cur.fetchone()
    emb = vector_literal(embedding)
    if row is None:
        # 2. by embedding, same type, same owner
        cur = await conn.execute(
            """
            SELECT id, embedding <=> %(e)s::vector AS d FROM entities
             WHERE employee_id IS NOT DISTINCT FROM %(emp)s AND type = %(type)s
             ORDER BY embedding <=> %(e)s::vector LIMIT 1
            """,
            {"e": emb, "emp": employee_id, "type": ref.type},
        )
        near = await cur.fetchone()
        if near is not None and near["d"] is not None and near["d"] <= SAME_ENTITY_DISTANCE:
            row = near
    if row is not None:
        await conn.execute(
            """
            UPDATE entities
               SET aliases = ARRAY(SELECT DISTINCT a FROM unnest(aliases || %(names)s) a
                                   WHERE a <> norm_name),
                   updated_at = now()
             WHERE id = %(id)s
            """,
            {"id": row["id"], "names": names},
        )
        return Resolved(row["id"], created=False)
    # 3. a new entity
    new_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO entities (id, tenant_id, employee_id, name, norm_name, type, aliases, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
        ON CONFLICT (tenant_id, coalesce(employee_id, ''), norm_name) DO NOTHING
        """,
        (new_id, tenant_id, employee_id, ref.name.strip(), norm, ref.type, aliases, emb),
    )
    cur = await conn.execute(
        "SELECT id FROM entities WHERE employee_id IS NOT DISTINCT FROM %s AND norm_name = %s",
        (employee_id, norm),
    )
    got = await cur.fetchone()
    assert got is not None
    return Resolved(got["id"], created=got["id"] == new_id)


async def link(conn: Conn, tenant_id: uuid.UUID | str, memory_id: uuid.UUID,
               entity_ids: list[uuid.UUID]) -> None:  # fmt: skip
    for eid in dict.fromkeys(entity_ids):
        await conn.execute(
            "INSERT INTO memory_entities (memory_id, entity_id, tenant_id) VALUES (%s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            (memory_id, eid, tenant_id),
        )


async def add_relation(
    conn: Conn,
    tenant_id: uuid.UUID | str,
    employee_id: str | None,
    src: uuid.UUID,
    type_: str,
    dst: uuid.UUID,
    *,
    memory_id: uuid.UUID | None,
    trust: str,
    confidence: float,
    relation_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Add a relation unless the same one already holds. Returns its id, or None if it was
    already there (or would be a self-loop)."""
    if src == dst:
        return None
    cur = await conn.execute(
        "SELECT 1 FROM relations WHERE src_id = %s AND type = %s AND dst_id = %s"
        " AND valid_to IS NULL",
        (src, type_, dst),
    )
    if await cur.fetchone() is not None:
        return None
    rid = relation_id or uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO relations (id, tenant_id, employee_id, src_id, type, dst_id, memory_id,
            confidence, trust)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (rid, tenant_id, employee_id, src, type_, dst, memory_id, confidence, trust),
    )
    return rid


async def close_relations_of(conn: Conn, memory_id: uuid.UUID, at: datetime | None = None) -> int:
    """The fact stopped holding: so do the relations it stated (history kept)."""
    cur = await conn.execute(
        "UPDATE relations SET valid_to = greatest(coalesce(%s, now()),"
        " valid_from + interval '1 microsecond')"
        " WHERE memory_id = %s AND valid_to IS NULL",
        (at, memory_id),
    )
    return cur.rowcount
