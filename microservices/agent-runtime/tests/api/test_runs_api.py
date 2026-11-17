"""RunService over real gRPC, against a migrated run_db (just up)."""

import uuid
from collections.abc import AsyncIterator

import grpc
import pytest

from wfk.agent_runtime.v1 import runs_pb2, runs_pb2_grpc
from wfk.common.v1 import common_pb2
from wfk_platform.db import Database

from agent_runtime.adapters.ledger import Ledger
from agent_runtime.app import create_grpc_server
from agent_runtime.settings import Settings
from agent_runtime.workflows.types import LedgerEntry, StartRecord

from ..conftest import OTHER_TENANT, TENANT

pytestmark = pytest.mark.integration


@pytest.fixture
async def stub(run_db: Database) -> AsyncIterator[runs_pb2_grpc.RunServiceStub]:
    server, _, port = create_grpc_server(Settings(http_host="127.0.0.1"), run_db, port=0)
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield runs_pb2_grpc.RunServiceStub(channel)
    await server.stop(grace=None)


async def seed(db: Database, tenant: str = TENANT, steps: int = 5) -> str:
    """A finished run with `steps` ledger entries, written the way the workflow writes them."""
    ledger = Ledger(db, "//cell-local/agent-runtime")
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    await ledger.start(StartRecord(run_id=run_id, tenant_id=tenant, employee_id="sam", task="x"))
    for seq in range(steps):
        last = seq == steps - 1
        await ledger.record(
            LedgerEntry(
                run_id=run_id, tenant_id=tenant, employee_id="sam", seq=seq,
                kind="final" if last else "tool_calls", decision={"kind": "x", "n": seq},
                results=[{"call_id": f"c{seq}", "content": "ok"}], model="fake-1",
                prompt_hash="a" * 64, input_tokens=10, output_tokens=2, cost_usd=0.001,
                step=seq + 1, status="completed" if last else "running",
                stop_reason="final" if last else "", output="Done." if last else None,
                total_input_tokens=10 * (seq + 1), total_output_tokens=2 * (seq + 1),
                total_cost_usd=0.001 * (seq + 1),
            )
        )  # fmt: skip
    return run_id


def tenant(t: str = TENANT) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(tenant_id=t)


async def code_of(call: object) -> grpc.StatusCode:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await call  # type: ignore[misc]
    return caught.value.code()


async def test_get_run(stub: runs_pb2_grpc.RunServiceStub, run_db: Database) -> None:
    run_id = await seed(run_db)
    resp = await stub.GetRun(runs_pb2.GetRunRequest(tenant=tenant(), run_id=run_id))
    run = resp.run
    assert (run.run_id, run.employee_id, run.status) == (
        run_id,
        "sam",
        runs_pb2.RUN_STATUS_COMPLETED,
    )
    assert (run.stop_reason, run.output, run.steps) == ("final", "Done.", 5)
    assert (run.input_tokens, run.output_tokens, run.cost_usd) == (50, 10, "0.005000")
    assert run.created_at.seconds > 0


async def test_another_tenants_run_looks_like_no_run(
    stub: runs_pb2_grpc.RunServiceStub, run_db: Database
) -> None:
    run_id = await seed(run_db, tenant=OTHER_TENANT)
    req = runs_pb2.GetRunRequest(tenant=tenant(TENANT), run_id=run_id)
    assert await code_of(stub.GetRun(req)) == grpc.StatusCode.NOT_FOUND
    missing = runs_pb2.GetRunRequest(tenant=tenant(), run_id="no-such-run")
    assert await code_of(stub.GetRun(missing)) == grpc.StatusCode.NOT_FOUND
    steps = runs_pb2.ListStepsRequest(tenant=tenant(TENANT), run_id=run_id)
    assert await code_of(stub.ListSteps(steps)) == grpc.StatusCode.NOT_FOUND


async def test_bad_requests_are_rejected(stub: runs_pb2_grpc.RunServiceStub) -> None:
    bad_tenant = runs_pb2.GetRunRequest(tenant=tenant("acme"), run_id="r")
    assert await code_of(stub.GetRun(bad_tenant)) == grpc.StatusCode.INVALID_ARGUMENT
    no_run = runs_pb2.GetRunRequest(tenant=tenant())
    assert await code_of(stub.GetRun(no_run)) == grpc.StatusCode.INVALID_ARGUMENT
    bad_token = runs_pb2.ListStepsRequest(
        tenant=tenant(), run_id="r", page=common_pb2.PageRequest(page_token="!!")
    )
    assert await code_of(stub.ListSteps(bad_token)) == grpc.StatusCode.INVALID_ARGUMENT
    too_big = runs_pb2.ListStepsRequest(
        tenant=tenant(), run_id="r", page=common_pb2.PageRequest(page_size=500)
    )
    assert await code_of(stub.ListSteps(too_big)) == grpc.StatusCode.INVALID_ARGUMENT


async def test_list_steps_pages_through_the_ledger(
    stub: runs_pb2_grpc.RunServiceStub, run_db: Database
) -> None:
    run_id = await seed(run_db, steps=5)
    seen: list[int] = []
    token, pages = "", 0
    while True:
        resp = await stub.ListSteps(
            runs_pb2.ListStepsRequest(
                tenant=tenant(),
                run_id=run_id,
                page=common_pb2.PageRequest(page_size=2, page_token=token),
            )
        )
        pages += 1
        seen += [s.seq for s in resp.steps]
        token = resp.page.next_page_token
        if not token:
            break
    assert seen == [0, 1, 2, 3, 4]
    assert pages == 3

    first = (await stub.ListSteps(runs_pb2.ListStepsRequest(tenant=tenant(), run_id=run_id))).steps[
        0
    ]
    assert (first.kind, first.model, len(first.prompt_hash)) == ("tool_calls", "fake-1", 64)
    assert dict(first.decision) == {"kind": "x", "n": 0}
    assert dict(first.results[0]) == {"call_id": "c0", "content": "ok"}
