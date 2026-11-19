"""The activity contract, observed through whole runs on the local stack (just up)."""

import uuid
from datetime import timedelta

import pytest
from agent_harness.errors import AuthenticationError, RateLimitError
from agent_harness.tools import Tool
from agent_harness.types import ToolUseBlock
from temporalio.client import Client

from core_python.agent_runtime.claim_check import ENCODING, ClaimCheckCodec, S3BlobStore
from core_python.agent_runtime.harness.state import EmployeeSpec, Limits
from core_python.agent_runtime.settings import Settings
from core_python.agent_runtime.worker import connect
from core_python.agent_runtime.workflows.employee_run import EmployeeRun
from core_python.agent_runtime.workflows.types import RunInput, RunOutcome

from .conftest import TEMPORAL, TENANT, World, fake, new_id, running_workers

pytestmark = pytest.mark.integration


async def run(client: Client, queue: str) -> tuple[RunInput, RunOutcome]:
    inp = RunInput(run_id=new_id(), tenant_id=TENANT, employee_id="sam", task="Where is 42?")
    out = await client.execute_workflow(
        EmployeeRun.run,
        inp,
        id=inp.run_id,
        task_queue=queue,
        execution_timeout=timedelta(seconds=90),
    )
    return inp, out


async def test_rate_limit_is_retried_and_the_run_completes(client: Client) -> None:
    provider = fake(RateLimitError("slow down", provider="x"), "All good.")
    async with running_workers(client, World(), provider) as queue:
        _, out = await run(client, queue)
    assert (out.state.status, out.state.output) == ("completed", "All good.")
    assert len(provider.requests) == 2, "retried once after the 429"


async def test_bad_key_fails_the_run_without_retrying(client: Client) -> None:
    provider = fake(AuthenticationError("invalid api key", provider="x"), "never reached")
    async with running_workers(client, World(), provider) as queue:
        _, out = await run(client, queue)
    assert (out.state.status, out.state.stop_reason) == ("failed", "error")
    assert out.state.output is not None and "AuthenticationError" in out.state.output
    assert len(provider.requests) == 1, "a bad key is never retried"


async def test_unknown_employee_fails_the_run(client: Client) -> None:
    async with running_workers(client, World(), fake()) as queue:
        inp = RunInput(run_id=new_id(), tenant_id=TENANT, employee_id="nobody", task="x")
        out = await client.execute_workflow(
            EmployeeRun.run,
            inp,
            id=inp.run_id,
            task_queue=queue,
            execution_timeout=timedelta(seconds=30),
        )
    assert (out.state.status, out.state.stop_reason) == ("failed", "error")
    assert "EmployeeNotFound" in (out.state.output or "")


async def test_large_tool_output_goes_to_s3_not_into_history() -> None:
    s = Settings()
    store = S3BlobStore(
        endpoint=s.s3_endpoint,
        bucket=s.payload_bucket,
        access_key=s.s3_access_key,
        secret_key=s.s3_secret_key,
        prefix=f"test-{uuid.uuid4().hex[:8]}/",
    )
    try:
        client = await connect(TEMPORAL, codec=ClaimCheckCodec(store, threshold=64 * 1024))
    except Exception as exc:
        pytest.skip(f"Temporal not running (just up): {exc}")

    def dump(size: int) -> str:
        """Return a big report.

        Args:
            size: characters.
        """
        return "z" * size

    def employee() -> EmployeeSpec:
        return EmployeeSpec.build(
            name="Sam",
            instructions="",
            model="fake-1",
            tools=[Tool(dump, max_output_chars=400_000)],  # default caps output at 40k
            tiers={"dump": "R0"},
            limits=Limits(max_steps=5),
        )

    provider = fake(ToolUseBlock(id="c1", name="dump", input={"size": 300_000}), "Done.")
    async with running_workers(client, World(), provider, employee=employee) as queue:
        inp, out = await run(client, queue)

    assert out.state.status == "completed"
    assert len(out.state.messages[2].content[0].content) > 300_000  # type: ignore[union-attr]

    history = await client.get_workflow_handle(inp.run_id).fetch_history()
    sizes, claimed = [], 0
    for event in history.events:
        for field in (
            event.activity_task_completed_event_attributes.result,
            event.activity_task_scheduled_event_attributes.input,
            event.workflow_execution_completed_event_attributes.result,
        ):
            for p in field.payloads:
                sizes.append(p.ByteSize())
                claimed += p.metadata.get("encoding") == ENCODING
    assert claimed >= 1, "the big payloads were replaced by references"
    assert max(sizes) <= 64 * 1024, "nothing over the threshold is stored in history"
