"""Memories in brain_db: storing them, and the vector and full-text retrievers.

Both retrievers return only what this employee may see (its own memories and tenant
knowledge) as it stood at `as_of`: recorded by then, and valid then. Row-level security adds
the tenant boundary underneath.
"""

from __future__ import annotations

from typing import Any, LiteralString

from wfk_platform.db import Conn, Database

from core_python.brain.domain.memory.model import Memory
from core_python.brain.domain.recall.retrieval import RecallQuery

COLUMNS: LiteralString = (
    "id, tenant_id, employee_id, scope, kind, content, importance, confidence, trust,"
    " source_ref, valid_from, valid_to, recorded_at"
)

# Visible to this employee, approved (not quarantined), and true and known at as_of.
VISIBLE: LiteralString = """
    (employee_id = %(employee_id)s OR scope = 'tenant')
    AND status = 'active'
    AND kind = ANY(%(kinds)s)
    AND recorded_at <= %(as_of)s
    AND valid_from <= %(as_of)s
    AND (valid_to IS NULL OR valid_to > %(as_of)s)
"""


def vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in embedding) + "]"


def _params(q: RecallQuery) -> dict[str, Any]:
    return {
        "employee_id": q.employee_id,
        "kinds": list(q.kinds),
        "as_of": q.as_of,
        "limit": q.limit,
        "text": q.text,
        "embedding": vector_literal(q.embedding),
    }


async def insert(conn: Conn, memory: Memory, embedding: list[float]) -> None:
    await conn.execute(
        f"INSERT INTO memories ({COLUMNS}, embedding) VALUES"  # noqa: S608 - constant columns
        " (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
        (
            memory.id, memory.tenant_id, memory.employee_id, memory.scope, memory.kind,
            memory.content, memory.importance, memory.confidence, memory.trust,
            memory.source_ref, memory.valid_from, memory.valid_to, memory.recorded_at,
            vector_literal(embedding),
        ),
    )  # fmt: skip


class MemoryRetrievers:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def vector(self, q: RecallQuery) -> list[Memory]:
        """Nearest by cosine distance (HNSW). Iterative scan keeps filtered results complete."""
        async with self.db.tenant_tx(q.tenant_id) as conn:
            await conn.execute("SET LOCAL hnsw.ef_search = 100")
            await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM memories WHERE {VISIBLE}"  # noqa: S608 - constants
                " ORDER BY embedding <=> %(embedding)s::vector LIMIT %(limit)s",
                _params(q),
            )
            return [Memory.model_validate(r) for r in await cur.fetchall()]

    async def fulltext(self, q: RecallQuery) -> list[Memory]:
        """Postgres full-text search, ranked by cover density."""
        async with self.db.tenant_tx(q.tenant_id) as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM memories,"  # noqa: S608 - constants
                " websearch_to_tsquery('english', %(text)s) AS query"
                f" WHERE tsv @@ query AND {VISIBLE}"
                " ORDER BY ts_rank_cd(tsv, query) DESC, recorded_at DESC LIMIT %(limit)s",
                _params(q),
            )
            return [Memory.model_validate(r) for r in await cur.fetchall()]
