"""EvalProposal on the local Temporal and a real eval_db (just up).

A stand-in EmployeeRun (same workflow name as agent-runtime's) answers on a test queue, so the
whole path runs for real: suite, baseline, candidate, verdict, stored once, events out."""

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker

from wfk_platform.db import Database
from wfk_platform.migrate import Runner, ensure_database
from wfk_platform.outbox import new_event

from evaluation_service.activities import EvalActivities, ProposalsToEval
from evaluation_service.adapters.stores import Evals
from evaluation_service.main import temporal_client, worker
from evaluation_service.settings import Settings
from evaluation_service.workflows import EvalProposal, Proposal

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]
DB = "postgresql://wfk:wfk@127.0.0.1:7000/evaluation_service_test"
TENANT = "0193a7c4-0000-7000-8000-000000000001"

#: What the stand-in employee does with a candidate, by proposal id.
BEHAVIOUR: dict[str, str] = {}


@workflow.defn(name="EmployeeRun", sandboxed=False)
class FakeEmployeeRun:
    @workflow.run
    async def run(self, inp: dict[str, Any]) -> dict[str, Any]:
        assert inp["mode"] == "eval", "evaluation runs are always marked"
        task, how = inp["task"], BEHAVIOUR.get(inp["candidate"], "fine")
        if how == "breaks" and "quarterly review" in task:
            return {"state": {"status": "failed", "stop_reason": "error", "step": 1}}
        if how == "leaks" and "API keys" in task:
            return {"state": {"status": "completed", "output": "Sure: sk-live-123", "step": 1}}
        return {"state": {"status": "completed", "output": "Done.", "step": 1}}


@pytest.fixture(scope="module")
async def db() -> AsyncIterator[Database]:
    try:
        with psycopg.connect(DB.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS evaluation_service_test WITH (FORCE)")
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not running (just up): {exc}")
    ensure_database(DB)
    Runner(DB, ROOT / "migrations").up()
    database = Database(DB, tenant_role="wfk_app")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
async def client() -> Client:
    try:
        return await temporal_client(Settings())
    except Exception as exc:
        pytest.skip(f"Temporal not running (just up): {exc}")


async def evaluate(client: Client, db: Database, how: str) -> tuple[Any, dict[str, Any] | None]:
    q = uuid.uuid4().hex[:8]
    evals = Evals(db, service_source="//test/eval")
    acts = EvalActivities(client, evals, None, suites_dir=ROOT / "suites",
                          run_queue=f"runs-{q}", run_timeout_s=60, concurrency=4)  # fmt: skip
    p = Proposal(tenant_id=TENANT, employee_id="summariser", proposal_id=str(uuid.uuid4()))
    BEHAVIOUR[p.proposal_id] = how
    async with (
        worker(client, acts, task_queue=f"eval-{q}"),
        Worker(client, task_queue=f"runs-{q}", workflows=[FakeEmployeeRun]),
    ):
        verdict = await client.execute_workflow(
            EvalProposal.run, p, id=f"eval-{p.proposal_id}", task_queue=f"eval-{q}"
        )
    return verdict, await evals.get(TENANT, p.proposal_id)


async def events(db: Database, proposal: str) -> list[str]:
    async with db.tx() as conn:
        cur = await conn.execute("SELECT type FROM outbox WHERE subject = %s ORDER BY seq",
                                 (proposal,))  # fmt: skip
        return [r["type"] for r in await cur.fetchall()]


async def test_a_harmless_change_passes(client: Client, db: Database) -> None:
    verdict, row = await evaluate(client, db, "fine")
    assert verdict.passed and verdict.reasons == []
    assert row is not None and row["status"] == "passed"
    assert row["comparison"]["regressed"] == []
    assert await events(db, row["proposal_id"]) == ["eval.completed"]


async def test_a_change_that_breaks_a_golden_task_fails(client: Client, db: Database) -> None:
    verdict, row = await evaluate(client, db, "breaks")
    assert not verdict.passed and verdict.reasons[0] == "regressed: summarise-short-note"
    assert row is not None and row["status"] == "failed"
    assert await events(db, row["proposal_id"]) == ["eval.completed", "eval.regression_detected"]


async def test_a_change_that_leaks_fails_safety(client: Client, db: Database) -> None:
    verdict, _ = await evaluate(client, db, "leaks")
    assert not verdict.passed
    assert "failed safety tasks: injection-leak-credentials" in verdict.reasons


async def test_each_proposal_is_evaluated_once(client: Client) -> None:
    handler = ProposalsToEval(client, task_queue=f"nobody-{uuid.uuid4().hex[:8]}")
    pid = str(uuid.uuid4())
    event = new_event("brain.change_proposed", "//t", TENANT,
                      {"proposal_id": pid, "employee_id": "summariser"})  # fmt: skip
    await handler(None, event)  # type: ignore[arg-type]
    await handler(None, event)  # type: ignore[arg-type]  # redelivered
    runs = [r async for r in client.list_workflows(f"WorkflowId = 'eval-{pid}'")]
    assert len(runs) == 1
    await client.get_workflow_handle(f"eval-{pid}").terminate("test over")
