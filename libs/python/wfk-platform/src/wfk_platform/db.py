"""PostgreSQL access with tenant isolation.

Every transaction that touches tenant data goes through `Database.tenant_tx`, which sets
`app.tenant_id` for that transaction only (the SET LOCAL equivalent). Row-level security
policies read it, so a query can never see another tenant's rows, even with a bug in the
WHERE clause.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

Conn = AsyncConnection[DictRow]
"""A connection whose rows come back as dicts."""


class Database:
    def __init__(self, url: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self.pool = AsyncConnectionPool[Conn](
            url,
            connection_class=Conn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row, "autocommit": False},
        )

    async def open(self, *, wait: bool = True) -> None:
        """Open the pool. With wait=False a service starts even while the database is down;
        readiness then reports it until the database comes back."""
        await self.pool.open(wait=wait)

    async def close(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[Conn]:
        """A transaction with no tenant set. For cross-tenant infrastructure work only
        (the outbox relay, migrations). Commits on success, rolls back on error."""
        async with self.pool.connection() as conn, conn.transaction():
            yield conn

    @asynccontextmanager
    async def tenant_tx(self, tenant_id: uuid.UUID | str) -> AsyncIterator[Conn]:
        """A transaction scoped to one tenant. Commits on success, rolls back on error."""
        tid = str(uuid.UUID(str(tenant_id)))  # rejects anything that is not a UUID
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tid,))
            yield conn

    async def ping(self) -> None:
        """Readiness check."""
        async with self.pool.connection() as conn:
            await conn.execute("SELECT 1")
