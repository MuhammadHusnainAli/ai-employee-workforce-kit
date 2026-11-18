"""Entity resolution, relations and memory links on a real brain_db (just up)."""

import uuid
from typing import Any

import pytest

from wfk_platform.db import Conn, Database

from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.adapters.postgres import graph
from brain_service.domain.graph.model import EntityRef

from ..conftest import OTHER_TENANT, TENANT

pytestmark = pytest.mark.integration
EMBED = HashEmbedder()


@pytest.fixture
def emp() -> str:
    return f"emp-{uuid.uuid4().hex[:8]}"


async def resolve(conn: Conn, emp: str | None, name: str, tenant: str = TENANT,
                  **kw: Any) -> graph.Resolved:  # fmt: skip
    ref = EntityRef(name=name, **kw)
    return await graph.resolve_entity(conn, tenant, emp, ref, EMBED.embed_one(ref.norm))


async def test_same_name_is_the_same_entity(brain_db: Database, emp: str) -> None:
    async with brain_db.tenant_tx(TENANT) as conn:
        first = await resolve(conn, emp, "Acme Corp", type="organization")
        again = await resolve(conn, emp, "The ACME corp.", type="organization")
    assert first.created and not again.created and first.id == again.id


async def test_aliases_resolve_both_ways(brain_db: Database, emp: str) -> None:
    async with brain_db.tenant_tx(TENANT) as conn:
        ana = await resolve(conn, emp, "Ana Lopez", type="person", aliases=["Ana"])
        by_alias = await resolve(conn, emp, "Ana", type="person")
        nickname = await resolve(conn, emp, "Annie", type="person", aliases=["Ana Lopez"])
        cur = await conn.execute("SELECT aliases FROM entities WHERE id = %s", (ana.id,))
        row = await cur.fetchone()
    assert by_alias.id == ana.id and nickname.id == ana.id
    assert row is not None and set(row["aliases"]) == {"ana", "annie"}, "learned the nickname"


async def test_different_names_are_different_entities(brain_db: Database, emp: str) -> None:
    async with brain_db.tenant_tx(TENANT) as conn:
        a = await resolve(conn, emp, "Project Zephyr", type="project")
        b = await resolve(conn, emp, "Project Apollo", type="project")
    assert a.id != b.id


async def test_employees_and_tenants_are_separate(brain_db: Database, emp: str) -> None:
    other_emp = f"emp-{uuid.uuid4().hex[:8]}"
    async with brain_db.tenant_tx(TENANT) as conn:
        mine = await resolve(conn, emp, "Bob")
        theirs = await resolve(conn, other_emp, "Bob")
    async with brain_db.tenant_tx(OTHER_TENANT) as conn:
        other_tenant = await resolve(conn, emp, "Bob", tenant=OTHER_TENANT)
        cur = await conn.execute("SELECT count(*) AS n FROM entities WHERE id = %s", (mine.id,))
        row = await cur.fetchone()
    assert len({mine.id, theirs.id, other_tenant.id}) == 3
    assert row is not None and row["n"] == 0, "RLS hides the other tenant's entity"


async def test_tenant_entities_are_shared_by_employees(brain_db: Database, emp: str) -> None:
    async with brain_db.tenant_tx(TENANT) as conn:
        shared = await resolve(conn, None, f"Globex {emp}", type="organization")
        seen_by_emp = await resolve(conn, emp, f"Globex {emp}", type="organization")
    assert seen_by_emp.id == shared.id


async def test_relations_are_unique_while_valid_and_close_with_their_fact(
    brain_db: Database, emp: str
) -> None:
    async with brain_db.tenant_tx(TENANT) as conn:
        ana = await resolve(conn, emp, "Ana", type="person")
        acme = await resolve(conn, emp, "Acme", type="organization")
        mid = uuid.uuid4()
        await conn.execute(
            "INSERT INTO memories (id, tenant_id, employee_id, scope, kind, content, trust)"
            " VALUES (%s, %s, %s, 'employee', 'semantic', 'Ana works at Acme.', 'observed')",
            (mid, TENANT, emp),
        )
        args = (conn, TENANT, emp, ana.id, "works_at", acme.id)
        rid = await graph.add_relation(*args, memory_id=mid, trust="observed", confidence=0.8)
        dup = await graph.add_relation(*args, memory_id=mid, trust="observed", confidence=0.8)
        loop = await graph.add_relation(conn, TENANT, emp, ana.id, "knows", ana.id,
                                        memory_id=None, trust="observed", confidence=1)  # fmt: skip
        await graph.link(conn, TENANT, mid, [ana.id, acme.id, ana.id])
    assert rid is not None and dup is None and loop is None
    async with brain_db.tenant_tx(TENANT) as conn:
        assert await graph.close_relations_of(conn, mid) == 1
        cur = await conn.execute("SELECT valid_to FROM relations WHERE id = %s", (rid,))
        row = await cur.fetchone()
        cur = await conn.execute(
            "SELECT count(*) AS n FROM memory_entities WHERE memory_id = %s", (mid,)
        )
        links = await cur.fetchone()
    assert row is not None and row["valid_to"] is not None, "closed, history kept"
    assert links is not None and links["n"] == 2
    async with brain_db.tenant_tx(TENANT) as conn:
        again = await graph.add_relation(
            conn, TENANT, emp, ana.id, "works_at", acme.id,
            memory_id=None, trust="observed", confidence=0.8,
        )  # fmt: skip
    assert again is not None, "once closed, the relation can hold again"
