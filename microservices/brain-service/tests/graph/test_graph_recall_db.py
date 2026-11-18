"""Graph recall on a real brain_db (just up): named entities, up to 2 hops, as of a time."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from wfk_platform.db import Database

from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.adapters.postgres.graph import GraphRetriever
from brain_service.domain.graph.model import EntityRef, RelationRef
from brain_service.domain.recall.retrieval import RecallQuery
from brain_service.domain.write.model import Candidate, Source
from brain_service.recall import Recall, RecallRequest
from brain_service.remember import Remember

from ..conftest import TENANT

pytestmark = pytest.mark.integration
EMBED = HashEmbedder()


@pytest.fixture
def emp() -> str:
    return f"emp-{uuid.uuid4().hex[:8]}"


async def learn(db: Database, emp: str, *cands: Candidate) -> list[uuid.UUID]:
    s = Source.model_validate(
        {"event_id": uuid.uuid4(), "event_type": "run.completed", "tenant_id": TENANT,
         "employee_id": emp, "task": "t", "outcome": "success"}
    )  # fmt: skip
    remember = Remember(db, EMBED, default_pii="keep", service_source="//test/brain")
    return (await remember(s, list(cands))).memory_ids


def fact(text: str, *names: str, rel: tuple[str, str, str] | None = None,
         key: str | None = None) -> Candidate:  # fmt: skip
    return Candidate(
        kind="semantic", content=text, key=key,
        mentions=[EntityRef(name=n) for n in names],
        relations=[RelationRef(subject=rel[0], type=rel[1], object=rel[2])] if rel else [],
    )  # fmt: skip


def query(emp: str, text: str, as_of: datetime | None = None) -> RecallQuery:
    return RecallQuery(tenant_id=TENANT, employee_id=emp, text=text, embedding=[],
                       kinds=("semantic", "episodic"), as_of=as_of or datetime.now(UTC),
                       limit=20)  # fmt: skip


async def chain(db: Database, emp: str) -> dict[str, uuid.UUID]:
    """Ana -works_at-> Acme -owns-> Zephyr -uses-> Kafka, each stated by its own fact, plus
    facts about each entity that share no words with a question about Ana."""
    ids: dict[str, uuid.UUID] = {}
    (ids["r1"],) = await learn(db, emp, fact("Ana works at Acme.", "Ana", "Acme",
                                              rel=("Ana", "works_at", "Acme")))  # fmt: skip
    (ids["r2"],) = await learn(db, emp, fact("Acme owns Zephyr.", "Acme", "Zephyr",
                                              rel=("Acme", "owns", "Zephyr")))  # fmt: skip
    (ids["r3"],) = await learn(db, emp, fact("Zephyr uses Kafka.", "Zephyr", "Kafka",
                                              rel=("Zephyr", "uses", "Kafka")))  # fmt: skip
    (ids["zephyr"],) = await learn(db, emp, fact("Its launch slipped to Friday.", "Zephyr"))
    (ids["kafka"],) = await learn(db, emp, fact("The cluster runs version 4.", "Kafka"))
    return ids


async def test_two_hops_and_no_further(brain_db: Database, emp: str) -> None:
    ids = await chain(brain_db, emp)
    found = [m.id for m in await GraphRetriever(brain_db)(query(emp, "What is Ana doing?"))]
    assert ids["zephyr"] in found, "Ana -> Acme -> Zephyr is two hops"
    assert ids["kafka"] not in found, "Kafka is three hops away"
    assert found.index(ids["r1"]) < found.index(ids["zephyr"]), "nearer first"


async def test_unknown_names_find_nothing(brain_db: Database, emp: str) -> None:
    await chain(brain_db, emp)
    retriever = GraphRetriever(brain_db)
    assert await retriever(query(emp, "What about Globex?")) == []
    assert await retriever(query(emp, "banana")) == [], "whole words only: no 'ana' in 'banana'"


async def test_as_of_before_the_relations_existed(brain_db: Database, emp: str) -> None:
    ids = await chain(brain_db, emp)
    before = datetime.now(UTC) - timedelta(days=1)
    found = [m.id for m in await GraphRetriever(brain_db)(query(emp, "Ana", as_of=before))]
    assert ids["zephyr"] not in found


async def test_closed_relations_are_not_walked(brain_db: Database, emp: str) -> None:
    ids = await chain(brain_db, emp)
    from brain_service.adapters.postgres.inspect import Inspector

    # A person corrects "Ana works at Acme": the works_at relation stops holding.
    await Inspector(brain_db, EMBED, service_source="//t").correct(
        TENANT, ids["r1"], "Ana left Acme.", by="maria", key="k"
    )
    found = [m.id for m in await GraphRetriever(brain_db)(query(emp, "Ana"))]
    assert ids["zephyr"] not in found, "Ana no longer works at Acme"


async def test_other_employees_graphs_are_separate(brain_db: Database, emp: str) -> None:
    await chain(brain_db, emp)
    other = f"emp-{uuid.uuid4().hex[:8]}"
    assert await GraphRetriever(brain_db)(query(other, "Ana")) == []


async def test_recall_fuses_the_graph_with_the_other_retrievers(
    brain_db: Database, emp: str
) -> None:
    ids = await chain(brain_db, emp)
    r = await Recall(brain_db, EMBED).recall(
        RecallRequest(tenant_id=TENANT, employee_id=emp, query="What is Ana doing?")
    )
    assert "graph" not in r.skipped
    assert ids["zephyr"] in [i.memory.id for i in r.items], "only the graph could find it"
