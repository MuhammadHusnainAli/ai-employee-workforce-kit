"""Consolidation on the local Temporal and brain_db (just up): workflows, decay, merges."""

import uuid
from typing import Any

import pytest
from temporalio.client import Client

from wfk_platform.db import Database

from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.adapters.postgres.consolidate import Consolidation
from brain_service.adapters.temporal.activities import ConsolidateActivities
from brain_service.adapters.temporal.consolidate import BrainConsolidate, ConsolidateAll, Employee
from brain_service.domain.write.model import Candidate, Source
from brain_service.ingest import batch_worker, temporal_client
from brain_service.remember import Remember
from brain_service.settings import Settings

from ..conftest import TENANT

pytestmark = pytest.mark.integration
EMBED = HashEmbedder()


@pytest.fixture
async def client() -> Client:
    try:
        return await temporal_client(Settings())
    except Exception as exc:
        pytest.skip(f"Temporal not running (just up): {exc}")


@pytest.fixture
def emp() -> str:
    return f"emp-{uuid.uuid4().hex[:8]}"


def remember(db: Database) -> Remember:
    return Remember(db, EMBED, default_pii="keep", service_source="//t")


async def episode(db: Database, emp: str, task: str, outcome: str) -> uuid.UUID:
    s = Source.model_validate({"event_id": uuid.uuid4(), "event_type": "run.completed",
                               "tenant_id": TENANT, "employee_id": emp, "run_id": "r",
                               "task": task, "outcome": outcome})  # fmt: skip
    ep = Candidate(kind="episodic", content=f"Task: {task}\nOutcome: {outcome}.", confidence=1.0)
    (mid,) = (await remember(db)(s, [ep])).memory_ids
    return mid


async def rows(db: Database, sql: str, *args: Any) -> list[dict[str, Any]]:
    async with db.tenant_tx(TENANT) as conn:
        cur = await conn.execute(sql, args)  # type: ignore[arg-type]
        return [dict(r) for r in await cur.fetchall()]


async def consolidate(client: Client, db: Database, emp: str) -> dict[str, int]:
    queue = f"brain-batch-test-{uuid.uuid4().hex[:8]}"
    async with batch_worker(client, ConsolidateActivities(Consolidation(db), remember(db)), queue):
        out = await client.execute_workflow(
            BrainConsolidate.run, Employee(tenant_id=TENANT, employee_id=emp),
            id=f"test-consolidate-{uuid.uuid4().hex[:8]}", task_queue=queue,
        )  # fmt: skip
    return out.counts


async def test_a_pass_learns_cited_facts_and_lessons_once(
    client: Client, brain_db: Database, emp: str
) -> None:
    task = "Reconcile the Acme invoices against the purchase orders"
    ids = [await episode(brain_db, emp, task, o) for o in ("failure", "failure", "success")]
    await episode(brain_db, emp, "Book a meeting room for Friday", "success")
    due = await rows(brain_db, "SELECT 1 FROM consolidation_due WHERE employee_id = %s", emp)
    assert due, "a new episode marks the employee due"

    counts = await consolidate(client, brain_db, emp)
    assert (counts["episodes"], counts["learned"]) == (4, 2)
    learned = await rows(
        brain_db,
        "SELECT kind, content, trust, metadata FROM memories WHERE employee_id = %s"
        " AND kind <> 'episodic' ORDER BY kind",
        emp,
    )
    assert [m["kind"] for m in learned] == ["reflection", "semantic"]
    fact = learned[1]
    assert "3 tasks like" in fact["content"] and fact["trust"] == "observed"
    assert sorted(fact["metadata"]["cites"]) == sorted(str(i) for i in ids)
    assert await rows(brain_db, "SELECT 1 FROM consolidation_due WHERE employee_id = %s", emp) == []

    again = await consolidate(client, brain_db, emp)
    assert (again["episodes"], again["learned"]) == (0, 0), "the watermark moved on"


async def test_unused_memories_decay_and_fall_to_the_archive(
    client: Client, brain_db: Database, emp: str
) -> None:
    s = Source.model_validate({"event_id": uuid.uuid4(), "event_type": "x", "tenant_id": TENANT,
                               "employee_id": emp, "task": "t"})  # fmt: skip
    done = await remember(brain_db)(s, [
        Candidate(kind="semantic", content="The old fax number is 555 0100.", importance=0.052),
        Candidate(kind="semantic", content="Acme is a key account.", importance=0.6),
        Candidate(kind="semantic", content="Ana approves refunds.", origin="feedback",
                  importance=0.03),
    ])  # fmt: skip
    fax, acme, human = done.memory_ids
    async with brain_db.tenant_tx(TENANT) as conn:
        await conn.execute(
            "UPDATE memories SET recorded_at = now() - interval '40 days', last_used_at = NULL"
            " WHERE employee_id = %s",
            (emp,),
        )
    counts = await consolidate(client, brain_db, emp)
    assert counts["decayed"] == 2 and counts["archived"] == 1
    found = await rows(
        brain_db, "SELECT id, status, importance FROM memories WHERE employee_id = %s", emp
    )
    by_id = {r["id"]: r for r in found}
    assert by_id[fax]["status"] == "archived"
    assert by_id[acme]["importance"] == pytest.approx(0.57)
    assert (by_id[human]["status"], by_id[human]["importance"]) == ("active", pytest.approx(0.03))


async def test_duplicate_entities_merge(client: Client, brain_db: Database, emp: str) -> None:
    keep, dup, acme = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mid = await episode(brain_db, emp, "Call Annie about the Acme renewal", "success")
    async with brain_db.tenant_tx(TENANT) as conn:
        for eid, name, norm, aliases, typ in [
            (keep, "Ana Lopez", "ana lopez", ["annie"], "person"),
            (dup, "Annie", "annie", [], "person"),
            (acme, "Acme", "acme", [], "organization"),
        ]:
            await conn.execute(
                "INSERT INTO entities (id, tenant_id, employee_id, name, norm_name, type, aliases,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, now() + %s * interval '1 s')",
                (eid, TENANT, emp, name, norm, typ, aliases, 0 if eid == keep else 1),
            )
        await conn.execute(
            "INSERT INTO memory_entities (memory_id, entity_id, tenant_id) VALUES (%s, %s, %s)",
            (mid, dup, TENANT),
        )
        for src, dst in [(dup, acme), (dup, keep)]:
            await conn.execute(
                "INSERT INTO relations (id, tenant_id, employee_id, src_id, type, dst_id, trust)"
                " VALUES (%s, %s, %s, %s, 'knows', %s, 'observed')",
                (uuid.uuid4(), TENANT, emp, src, dst),
            )
    counts = await consolidate(client, brain_db, emp)
    assert counts["merged_entities"] == 1
    assert await rows(brain_db, "SELECT 1 FROM entities WHERE id = %s", dup) == []
    links = await rows(brain_db, "SELECT entity_id FROM memory_entities WHERE memory_id = %s", mid)
    assert keep in [r["entity_id"] for r in links]
    rels = await rows(brain_db, "SELECT src_id, dst_id FROM relations WHERE employee_id = %s", emp)
    assert [(r["src_id"], r["dst_id"]) for r in rels] == [(keep, acme)], "no self-loop left"


async def test_the_nightly_run_visits_every_due_employee(
    client: Client, brain_db: Database, emp: str
) -> None:
    other = f"emp-{uuid.uuid4().hex[:8]}"
    for who in (emp, other):
        await episode(brain_db, who, "Answer the pricing question", "success")
        await episode(brain_db, who, "Answer the pricing question again", "success")
    queue = f"brain-batch-test-{uuid.uuid4().hex[:8]}"
    async with batch_worker(
        client, ConsolidateActivities(Consolidation(brain_db), remember(brain_db)), queue
    ):
        visited = await client.execute_workflow(
            ConsolidateAll.run, id=f"test-all-{uuid.uuid4().hex[:8]}", task_queue=queue
        )
    assert visited >= 2
    for who in (emp, other):
        done = await rows(brain_db, "SELECT counts FROM consolidations WHERE employee_id = %s", who)
        assert done and done[0]["counts"]["episodes"] == 2
