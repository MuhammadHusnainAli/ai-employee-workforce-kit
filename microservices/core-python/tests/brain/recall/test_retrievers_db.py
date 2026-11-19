"""Vector and full-text retrievers on a real brain_db (just up)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from wfk_platform.db import Database

from core_python.brain.adapters.modelgw.embeddings import HashEmbedder
from core_python.brain.adapters.postgres import memories as repo
from core_python.brain.adapters.postgres.memories import MemoryRetrievers
from core_python.brain.domain.memory.model import Kind, Memory
from core_python.brain.domain.recall.retrieval import RecallQuery, retrieve

from ..conftest import OTHER_TENANT, TENANT

pytestmark = pytest.mark.integration
EMBED = HashEmbedder()
NOW = datetime.now(UTC)


async def add(db: Database, content: str, *, tenant: str = TENANT, **kw: object) -> Memory:
    m = Memory.model_validate(
        {"tenant_id": tenant, "employee_id": kw.pop("employee", "sam"), "kind": "semantic",
         "content": content, "trust": "system", **kw}
    )  # fmt: skip
    async with db.tenant_tx(tenant) as conn:
        await repo.insert(conn, m, EMBED.embed_one(content))
    return m


def q(text: str, employee: str, *, kinds: tuple[Kind, ...] = ("semantic", "episodic"),
      as_of: datetime | None = None) -> RecallQuery:  # fmt: skip
    return RecallQuery(
        tenant_id=TENANT,
        employee_id=employee,
        text=text,
        embedding=EMBED.embed_one(text),
        kinds=kinds,
        as_of=as_of or datetime.now(UTC),
        limit=10,
    )


@pytest.fixture
def emp() -> str:
    """A fresh employee per test, so tests never see each other's memories."""
    return f"emp-{uuid.uuid4().hex[:8]}"


async def test_both_retrievers_find_the_relevant_memory_first(brain_db: Database, emp: str) -> None:
    want = await add(
        brain_db, "Acme Corp gets a 30 day refund window on annual plans", employee=emp
    )
    await add(brain_db, "The weekly team meeting is on Mondays at ten", employee=emp)
    await add(brain_db, "Invoices are sent on the first business day", employee=emp)

    r = MemoryRetrievers(brain_db)
    query = q("refund window for Acme", emp)
    assert (await r.vector(query))[0].id == want.id
    full = await r.fulltext(query)
    assert [m.id for m in full] == [want.id], "full text only returns matching memories"


async def test_only_visible_memories_come_back(brain_db: Database, emp: str) -> None:
    mine = await add(brain_db, "pricing: enterprise tier is per seat", employee=emp)
    shared = await add(brain_db, "pricing: all prices exclude VAT", employee=None, scope="tenant")
    await add(brain_db, "pricing: secret discount for Bob", employee="someone-else")
    await add(brain_db, "pricing: another tenant's price list", employee=emp, tenant=OTHER_TENANT)

    r = MemoryRetrievers(brain_db)
    for found in (await r.vector(q("pricing", emp)), await r.fulltext(q("pricing", emp))):
        ids = {m.id for m in found}
        assert {mine.id, shared.id} <= ids
        assert all(m.employee_id in (emp, None) for m in found)
        assert all(str(m.tenant_id) == TENANT for m in found)


async def test_as_of_answers_what_was_known_and_true_then(brain_db: Database, emp: str) -> None:
    old = await add(
        brain_db,
        "office address is 1 Old Street",
        employee=emp,
        valid_from=NOW - timedelta(days=60),
        valid_to=NOW - timedelta(days=10),
        recorded_at=NOW - timedelta(days=60),
    )
    new = await add(
        brain_db,
        "office address is 9 New Road",
        employee=emp,
        valid_from=NOW - timedelta(days=10),
        recorded_at=NOW - timedelta(days=10),
    )
    r = MemoryRetrievers(brain_db)

    today = {m.id for m in await r.fulltext(q("office address", emp))}
    assert today == {new.id}, "a superseded fact is not true now"

    then = {
        m.id for m in await r.fulltext(q("office address", emp, as_of=NOW - timedelta(days=30)))
    }
    assert then == {old.id}, "a month ago, only the old address was known and true"


async def test_kind_filter_and_parallel_run(brain_db: Database, emp: str) -> None:
    await add(brain_db, "lesson: legal review takes a week", employee=emp, kind="reflection")
    episode = await add(
        brain_db, "handled the legal review for Acme", employee=emp, kind="episodic"
    )
    r = MemoryRetrievers(brain_db)
    out = await retrieve(
        q("legal review", emp, kinds=("episodic",)),
        {"vector": r.vector, "fulltext": r.fulltext},
    )
    assert not out.degraded
    assert {m.id for m in out.memories().values()} == {episode.id}
    assert all(rep.ms < 150 for rep in out.reports.values())
