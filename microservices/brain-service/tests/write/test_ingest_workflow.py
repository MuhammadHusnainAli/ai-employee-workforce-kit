"""BrainIngest on the local Temporal and brain_db (just up), fed the way the run consumer does."""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from agent_harness import FakeProvider
from temporalio.client import Client

from wfk_platform.db import Database
from wfk_platform.outbox import CloudEvent, new_event

from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.adapters.modelgw.extractor import Extractor, GatewayExtractor
from brain_service.adapters.temporal.activities import IngestActivities
from brain_service.adapters.temporal.ingest import BrainIngest
from brain_service.domain.write.model import Committed
from brain_service.ingest import RunsToIngest, temporal_client, worker
from brain_service.remember import Remember
from brain_service.settings import Settings

from ..conftest import TENANT

pytestmark = pytest.mark.integration

EXTRACTED = json.dumps(
    {
        "facts": [],
        "preferences": [{"content": "Ana prefers Tuesday meetings.", "key": "ana.meeting_day"}],
        "lessons": [],
    }
)


@pytest.fixture
async def client() -> Client:
    try:
        return await temporal_client(Settings())
    except Exception as exc:
        pytest.skip(f"Temporal not running (just up): {exc}")


@pytest.fixture
def emp() -> str:
    return f"emp-{uuid.uuid4().hex[:8]}"


@asynccontextmanager
async def running(
    client: Client, db: Database, extractor: Extractor
) -> AsyncIterator[tuple[str, RunsToIngest]]:
    queue = f"brain-ingest-test-{uuid.uuid4().hex[:8]}"
    remember = Remember(db, HashEmbedder(), default_pii="redact", service_source="//test/brain")
    async with worker(client, IngestActivities(extractor, remember), task_queue=queue):
        yield queue, RunsToIngest(client, queue)


def run_completed(emp: str, **data: Any) -> CloudEvent:
    base = {"run_id": "r1", "employee_id": emp, "seq": 2, "status": "completed",
            "stop_reason": "final", "task": "Book the review with Ana.",
            "output": "Booked for Tuesday."}  # fmt: skip
    return new_event("run.completed", "//test/agent-runtime", TENANT, base | data, subject="r1")


async def memories(db: Database, emp: str) -> list[dict[str, Any]]:
    async with db.tenant_tx(TENANT) as conn:
        cur = await conn.execute(
            "SELECT kind, content, fact_key FROM memories WHERE employee_id = %s ORDER BY kind",
            (emp,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def test_a_finished_run_is_remembered_once(
    client: Client, brain_db: Database, emp: str
) -> None:
    extractor = GatewayExtractor(FakeProvider([EXTRACTED]), "claude-haiku")
    async with running(client, brain_db, extractor) as (_, to_ingest):
        event = run_completed(emp)
        async with brain_db.tenant_tx(TENANT) as conn:
            await to_ingest(conn, event)
            await to_ingest(conn, event)  # redelivered: still one workflow
        handle = client.get_workflow_handle(f"brain-ingest-{event.id}", result_type=Committed)
        done = await handle.result()

    assert done.counts["inserted"] == 2
    stored = await memories(brain_db, emp)
    assert [(m["kind"], m["fact_key"]) for m in stored] == [
        ("episodic", None),
        ("semantic", "ana.meeting_day"),
    ]
    assert "Book the review with Ana." in stored[0]["content"]
    runs = [r async for r in client.list_workflows(f"WorkflowId = 'brain-ingest-{event.id}'")]
    assert len(runs) == 1


async def test_events_with_nothing_to_learn_start_nothing(
    client: Client, brain_db: Database, emp: str
) -> None:
    async with running(client, brain_db, GatewayExtractor(FakeProvider([]), "m")) as (_, to_ingest):
        cancelled = run_completed(emp, stop_reason="cancelled")
        cancelled.type = "run.failed"
        step = new_event("run.step_completed", "//t", TENANT, {"employee_id": emp}, subject="r")
        async with brain_db.tenant_tx(TENANT) as conn:
            for e in (cancelled, step):
                await to_ingest(conn, e)
        for e in (cancelled, step):
            found = [r async for r in client.list_workflows(f"WorkflowId = 'brain-ingest-{e.id}'")]
            assert found == []


async def test_model_outage_is_retried_and_then_remembered(
    client: Client, brain_db: Database, emp: str
) -> None:
    class Flaky:
        """Fails once, like a gateway timeout, then answers."""

        def __init__(self) -> None:
            self.calls = 0
            self.inner = GatewayExtractor(FakeProvider([EXTRACTED]), "m")

        async def extract(self, source):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("gateway timeout")
            return await self.inner.extract(source)

    flaky = Flaky()
    async with running(client, brain_db, flaky) as (queue, _):  # type: ignore[arg-type]
        from brain_service.domain.write.sources import from_run_event

        event = run_completed(emp)
        source = from_run_event(event.type, event.id, event.tenantid, event.time, event.data)
        assert source is not None
        done = await client.execute_workflow(
            BrainIngest.run, source, id=f"brain-ingest-{event.id}", task_queue=queue
        )
    assert flaky.calls == 2
    assert done.counts["inserted"] == 2
