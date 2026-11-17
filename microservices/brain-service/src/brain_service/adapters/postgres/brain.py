"""Identity versions and the recall log in brain_db."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from wfk_platform.db import Conn, Database

from brain_service.domain.memory.identity import Identity


@dataclass(frozen=True)
class ActiveIdentity:
    version: int
    text: str


async def active_identity(conn: Conn, employee_id: str) -> ActiveIdentity | None:
    cur = await conn.execute(
        "SELECT version, identity_text FROM brain_versions"
        " WHERE employee_id = %s AND status = 'active'",
        (employee_id,),
    )
    row = await cur.fetchone()
    return ActiveIdentity(row["version"], row["identity_text"]) if row else None


async def activate_identity(
    db: Database, tenant_id: str, employee_id: str, identity: Identity, *, by: str
) -> int:
    """Store a new identity version and make it the active one; the old one is retired.
    One transaction, so there is never a moment with zero or two active versions."""
    async with db.tenant_tx(tenant_id) as conn:
        cur = await conn.execute(
            "SELECT coalesce(max(version), 0) + 1 AS v FROM brain_versions WHERE employee_id = %s",
            (employee_id,),
        )
        row = await cur.fetchone()
        version = int(row["v"]) if row else 1
        await conn.execute(
            "UPDATE brain_versions SET status = 'retired'"
            " WHERE employee_id = %s AND status = 'active'",
            (employee_id,),
        )
        await conn.execute(
            "INSERT INTO brain_versions (tenant_id, employee_id, version, status, identity,"
            " identity_text, created_by) VALUES (%s, %s, %s, 'active', %s, %s, %s)",
            (
                tenant_id, employee_id, version, Jsonb(identity.model_dump()),
                identity.render(), by,
            ),
        )  # fmt: skip
    return version


async def log_recall(
    conn: Conn,
    *,
    tenant_id: str,
    employee_id: str,
    run_id: str | None,
    step: int | None,
    query: str,
    items: list[tuple[str, float]],
    retrievers: dict[str, Any],
    degraded: bool,
) -> uuid.UUID:
    recall_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO recall_log (id, tenant_id, employee_id, run_id, step, query, item_ids,"
        " scores, retrievers, degraded) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            recall_id, tenant_id, employee_id, run_id, step, query,
            [uuid.UUID(i) for i, _ in items], [s for _, s in items], Jsonb(retrievers), degraded,
        ),
    )  # fmt: skip
    return recall_id
