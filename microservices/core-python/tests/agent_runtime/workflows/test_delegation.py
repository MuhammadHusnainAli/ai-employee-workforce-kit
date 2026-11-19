"""A manager's run waits for the work it handed down, durably, on the local stack (just up).

The sub-tasks themselves are not run here: their task.child_finished arrives as the
`child_finished` signal, exactly as agent-runtime's task inbox delivers it."""

import asyncio
from datetime import timedelta

import pytest
from agent_harness.llm_providers import CompletionRequest
from agent_harness.tools import Tool
from agent_harness.types import ToolResultBlock, ToolUseBlock
from temporalio.client import Client, WorkflowHandle

from core_python.agent_runtime.harness.reports import ChildReport
from core_python.agent_runtime.harness.state import EmployeeSpec, Limits, Tier
from core_python.agent_runtime.workflows.employee_run import EmployeeRun
from core_python.agent_runtime.workflows.types import RunInput, RunOutcome

from .conftest import TENANT, World, fake, new_id, running_workers, wait_for

pytestmark = pytest.mark.integration

DELEGATE = ToolUseBlock(
    id="d1",
    name="delegate_task",
    input={"employee_id": "junior", "title": "Book demos", "instructions": "x"},
)
WAIT = ToolUseBlock(id="w1", name="wait_for_reports", input={})


def manager() -> EmployeeSpec:
    async def delegate_task(employee_id: str, title: str, instructions: str) -> str:
        """Hand work down.

        Args:
            employee_id: who.
            title: what.
            instructions: how.
        """
        return f"Handed to {employee_id} as task t-1."

    async def wait_for_reports() -> str:
        """Wait for reports."""
        return "You have no handed-down tasks to wait for."

    tiers: dict[str, Tier] = {"delegate_task": "R1", "wait_for_reports": "R0"}
    return EmployeeSpec.build(
        name="Boss",
        instructions="Run sales.",
        model="fake-1",
        tools=[Tool(delegate_task), Tool(wait_for_reports)],
        tiers=tiers,
        limits=Limits(max_steps=10),
    )


def last_tool_text(req: CompletionRequest) -> str:
    for m in reversed(req.messages):
        for b in m.content:
            if isinstance(b, ToolResultBlock):
                return str(b.content)
    return ""


def last_user_text(req: CompletionRequest) -> str:
    return req.messages[-1].text


def report(status: str = "completed", output: str = "3 demos booked") -> ChildReport:
    return ChildReport(
        child_task_id="t-1",
        employee_id="junior",
        title="Book demos",
        status=status,
        stop_reason="final" if status == "completed" else "error",
        output=output,
    )


async def start(
    client: Client, queue: str, wait_s: float = 600
) -> WorkflowHandle[EmployeeRun, RunOutcome]:
    inp = RunInput(
        run_id=new_id(),
        tenant_id=TENANT,
        employee_id="sam",
        task="Book three demos this week.",
        wait_timeout_s=wait_s,
    )
    return await client.start_workflow(
        EmployeeRun.run,
        inp,
        id=inp.run_id,
        task_queue=queue,
        execution_timeout=timedelta(seconds=60),
    )


async def test_wait_for_reports_returns_what_the_team_did(client: Client) -> None:
    provider = fake(DELEGATE, WAIT, lambda req: "Summary: " + last_tool_text(req))
    async with running_workers(client, World(), provider, employee=manager) as queue:
        handle = await start(client, queue)
        await handle.signal(EmployeeRun.child_finished, report())
        out = await handle.result()
    assert out.state.status == "completed"
    assert out.state.output is not None and "3 demos booked" in out.state.output
    assert "junior" in out.state.output and "completed" in out.state.output


async def test_a_failed_sub_task_is_reported_as_failed(client: Client) -> None:
    provider = fake(DELEGATE, WAIT, lambda req: "Summary: " + last_tool_text(req))
    async with running_workers(client, World(), provider, employee=manager) as queue:
        handle = await start(client, queue)
        await handle.signal(EmployeeRun.child_finished, report("failed", ""))
        out = await handle.result()
    assert out.state.output is not None and "failed" in out.state.output


async def test_finishing_early_waits_for_the_team_and_carries_on(client: Client) -> None:
    provider = fake(DELEGATE, "I am done.", lambda req: "Final: " + last_user_text(req))
    async with running_workers(client, World(), provider, employee=manager) as queue:
        handle = await start(client, queue)
        await wait_for(handle, lambda s: s.step >= 1)
        await asyncio.sleep(0.5)  # the model has asked to finish by now; the run must wait
        state = await handle.query(EmployeeRun.state)
        assert state is not None and state.status == "running"
        await handle.signal(EmployeeRun.child_finished, report())
        out = await handle.result()
    assert out.state.status == "completed"
    assert out.state.output is not None and out.state.output.startswith("Final:")
    assert "3 demos booked" in out.state.output
    assert len(provider.requests) == 3


async def test_a_report_that_never_comes_ends_the_wait(client: Client) -> None:
    provider = fake(DELEGATE, WAIT, lambda req: "Summary: " + last_tool_text(req))
    async with running_workers(client, World(), provider, employee=manager) as queue:
        handle = await start(client, queue, wait_s=2)
        out = await handle.result()
    assert out.state.status == "completed", (out.state.stop_reason, out.state.output)
    assert out.state.output is not None and "still working" in out.state.output


async def test_a_run_that_hands_nothing_down_is_unchanged(client: Client) -> None:
    provider = fake(WAIT, "Nothing to do.")
    async with running_workers(client, World(), provider, employee=manager) as queue:
        handle = await start(client, queue)
        out = await handle.result()
    assert out.state.output == "Nothing to do."
    results = [b for m in out.state.messages for b in m.content if isinstance(b, ToolResultBlock)]
    assert "no handed-down tasks" in str(results[0].content)
