"""EmployeeRun on the local Temporal server (just up)."""

from datetime import timedelta

import pytest
from agent_harness.llm_providers.base import ModelInfo, register_model
from agent_harness.types import ToolUseBlock
from temporalio.client import Client

from agent_runtime.harness.state import Limits
from agent_runtime.workflows.employee_run import EmployeeRun
from agent_runtime.workflows.types import RunInput, RunOutcome

from .conftest import TENANT, World, fake, new_id, running_workers, wait_for

pytestmark = pytest.mark.integration


def run_input(task: str = "Where is order 42?") -> RunInput:
    return RunInput(run_id=new_id(), tenant_id=TENANT, employee_id="sam", task=task)


async def start_and_wait(client: Client, queue: str, inp: RunInput) -> RunOutcome:
    return await client.execute_workflow(
        EmployeeRun.run,
        inp,
        id=inp.run_id,
        task_queue=queue,
        execution_timeout=timedelta(seconds=60),
    )


async def test_run_completes_with_a_tool_call(client: Client) -> None:
    world = World()
    provider = fake(ToolUseBlock(id="c1", name="lookup", input={"order": 42}), "It ships Tuesday.")
    async with running_workers(client, world, provider) as queue:
        inp = run_input()
        outcome = await start_and_wait(client, queue, inp)

    state = outcome.state
    assert (state.status, state.stop_reason, state.output) == (
        "completed",
        "final",
        "It ships Tuesday.",
    )
    assert world.keys == [f"{inp.run_id}:0:0"], "the tool ran exactly once"
    assert state.step == 1
    assert len(provider.requests) == 2


async def test_invalid_call_is_repaired_inside_the_workflow(client: Client) -> None:
    world = World()
    provider = fake(
        ToolUseBlock(id="c1", name="lookup", input={"order": "forty-two"}),
        ToolUseBlock(id="c2", name="lookup", input={"order": 42}),
        "It ships Tuesday.",
    )
    async with running_workers(client, world, provider) as queue:
        inp = run_input()
        outcome = await start_and_wait(client, queue, inp)
    assert outcome.state.status == "completed"
    assert world.keys == [f"{inp.run_id}:1:0"]


async def test_budget_stop_waits_for_a_person_without_spending(client: Client) -> None:
    register_model(ModelInfo(id="pricey-wf-model", provider="fake", input_cost=50, output_cost=50))
    world = World()
    provider = fake("never reached")
    async with running_workers(
        client,
        world,
        provider,
        employee=lambda: world.employee(Limits(max_cost_usd=0.01), model="pricey-wf-model"),
    ) as queue:
        inp = run_input()
        handle = await client.start_workflow(
            EmployeeRun.run,
            inp,
            id=inp.run_id,
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
        waiting = await wait_for(handle, lambda s: s.status == "waiting_human")
        assert waiting.stop_reason == "budget"
        assert provider.requests == []
        await handle.signal(EmployeeRun.cancel, "budget not raised")
        outcome = await handle.result()
    assert (outcome.state.status, outcome.state.stop_reason) == ("failed", "cancelled")
