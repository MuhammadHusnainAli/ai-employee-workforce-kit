"""The write path builds the graph (just up): entities, links and relations with their facts."""

import uuid
from typing import Any

import pytest

from wfk_platform.db import Database

from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.adapters.postgres.inspect import Inspector
from brain_service.domain.graph.model import EntityRef, RelationRef
from brain_service.domain.write.model import Candidate, Source
from brain_service.remember import Remember

from ..conftest import TENANT

pytestmark = pytest.mark.integration
EMBED = HashEmbedder()


@pytest.fixture
def emp() -> str:
    return f"emp-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def remember(brain_db: Database) -> Remember:
    return Remember(brain_db, EMBED, default_pii="redact", service_source="//test/brain")


def src(emp: str) -> Source:
    return Source.model_validate(
        {"event_id": uuid.uuid4(), "event_type": "run.completed", "tenant_id": TENANT,
         "employee_id": emp, "task": "t", "outcome": "success"}
    )  # fmt: skip


def works_at(company: str, *, key: str = "ana.employer") -> Candidate:
    return Candidate(
        kind="semantic",
        content=f"Ana works at {company}.",
        key=key,
        mentions=[EntityRef(name="Ana", type="person"),
                  EntityRef(name=company, type="organization")],
        relations=[RelationRef(subject="Ana", type="works_at", object=company)],
    )  # fmt: skip


async def q(db: Database, sql: str, *args: Any) -> list[dict[str, Any]]:
    async with db.tenant_tx(TENANT) as conn:
        cur = await conn.execute(sql, args)  # type: ignore[arg-type]
        return [dict(r) for r in await cur.fetchall()]


async def relations(db: Database, emp: str) -> list[dict[str, Any]]:
    return await q(
        db,
        "SELECT s.name AS src, r.type, d.name AS dst, r.valid_to, r.memory_id, r.trust"
        " FROM relations r JOIN entities s ON s.id = r.src_id JOIN entities d ON d.id = r.dst_id"
        " WHERE r.employee_id = %s ORDER BY r.recorded_at",
        emp,
    )


async def test_a_fact_adds_entities_links_and_its_relation(
    brain_db: Database, remember: Remember, emp: str
) -> None:
    done = await remember(src(emp), [works_at("Acme")])
    assert (done.counts["entities"], done.counts["relations"]) == (2, 1)
    (mid,) = done.memory_ids
    linked = await q(
        brain_db,
        "SELECT e.name FROM memory_entities me JOIN entities e ON e.id = me.entity_id"
        " WHERE me.memory_id = %s ORDER BY e.name",
        mid,
    )
    assert [r["name"] for r in linked] == ["Acme", "Ana"]
    (rel,) = await relations(brain_db, emp)
    assert (rel["src"], rel["type"], rel["dst"], rel["memory_id"], rel["trust"]) == (
        "Ana", "works_at", "Acme", mid, "observed",
    )  # fmt: skip


async def test_the_same_names_resolve_to_the_same_entities(
    brain_db: Database, remember: Remember, emp: str
) -> None:
    await remember(src(emp), [works_at("Acme", key="k1")])
    again = await remember(src(emp), [
        Candidate(kind="semantic", content="Ana leads the Acme renewal.",
                  mentions=[EntityRef(name="ana"), EntityRef(name="The Acme")])
    ])  # fmt: skip
    assert again.counts["entities"] == 0
    rows = await q(brain_db, "SELECT count(*) AS n FROM entities WHERE employee_id = %s", emp)
    assert rows[0]["n"] == 2


async def test_superseding_a_fact_closes_its_relations(
    brain_db: Database, remember: Remember, emp: str
) -> None:
    await remember(src(emp), [works_at("Acme")])
    done = await remember(src(emp), [works_at("Globex")])
    assert done.counts["superseded"] == 1
    old, new = await relations(brain_db, emp)
    assert (old["dst"], old["valid_to"] is not None) == ("Acme", True)
    assert (new["dst"], new["valid_to"]) == ("Globex", None)


async def test_a_correction_keeps_the_entities_and_closes_old_relations(
    brain_db: Database, remember: Remember, emp: str
) -> None:
    (mid,) = (await remember(src(emp), [works_at("Acme")])).memory_ids
    inspector = Inspector(brain_db, EMBED, service_source="//test/brain")
    fixed = await inspector.correct(TENANT, mid, "Ana left Acme in June.", by="maria", key="k")
    (rel,) = await relations(brain_db, emp)
    assert rel["valid_to"] is not None
    linked = await q(
        brain_db, "SELECT count(*) AS n FROM memory_entities WHERE memory_id = %s", fixed.id
    )
    assert linked[0]["n"] == 2


async def test_deleting_a_memory_removes_its_links_and_relations(
    brain_db: Database, remember: Remember, emp: str
) -> None:
    (mid,) = (await remember(src(emp), [works_at("Acme")])).memory_ids
    inspector = Inspector(brain_db, EMBED, service_source="//test/brain")
    await inspector.delete(TENANT, mid, by="maria", reason="wrong")
    assert await relations(brain_db, emp) == []
    links = await q(brain_db, "SELECT 1 FROM memory_entities WHERE memory_id = %s", mid)
    assert links == []
