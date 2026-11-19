"""Approval, human reply, pause, resume and cancel on a live run (just up)."""

from datetime import timedelta
from typing import Any

import pytest
from agent_harness.llm_providers import CompletionRequest
from agent_harness.types import ToolResultBlock, ToolUseBlock
from temporalio.client import Client, WorkflowHandle

from core_python.agent_runtime.workflows.employee_run import EmployeeRun
from core_python.agent_runtime.workflows.types import ApprovalAnswer, RunInput, RunOutcome

from .conftest import TENANT, World, fake, new_id, running_workers, wait_for

pytestmark = pytest.mark.integration

LOOKUP = ToolUseBlock(id="c1", name="lookup", input={"order": 42})
EMAIL = ToolUseBlock(id="c2", name="send_email", input={"to": "ana@example.com", "body": "Tue"})


async def start(
    client: Client, queue: str, *, wait_timeout_s: float = 600, **kw: Any
) -> WorkflowHandle[EmployeeRun, RunOutcome]:
    inp = RunInput(
        run_id=new_id(),
        tenant_id=TENANT,
        employee_id="sam",
        task="Tell Ana where 42 is.",
        wait_timeout_s=wait_timeout_s,
    )
    return await client.start_workflow(
        EmployeeRun.run,
        inp,
        id=inp.run_id,
        task_queue=queue,
        execution_timeout=timedelta(seconds=60),
        **kw,
    )


def tool_results(outcome: RunOutcome) -> list[ToolResultBlock]:
    return [b for m in outcome.state.messages for b in m.content if isinstance(b, ToolResultBlock)]


async def test_r2_call_waits_for_approval_then_runs_once(client: Client) -> None:
    world = World()
    provider = fake(LOOKUP, EMAIL, "Done: Ana knows.")
    async with running_workers(client, world, provider) as queue:
        handle = await start(client, queue)
        waiting = await wait_for(handle, lambda s: s.status == "waiting_human")
        assert waiting.stop_reason == "approval" and [c.id for c in waiting.pending] == ["c2"]
        assert world.sent == [], "nothing is sent before a person approves"

        await handle.signal(EmployeeRun.approve, ApprovalAnswer(approved=True, by="maria"))
        outcome = await handle.result()

    assert (outcome.state.status, outcome.state.output) == ("completed", "Done: Ana knows.")
    assert world.sent == ["ana@example.com"]


async def test_declined_approval_is_an_answer_the_model_reads(client: Client) -> None:
    world = World()
    provider = fake(EMAIL, "Understood, I will not email Ana.")
    async with running_workers(client, world, provider) as queue:
        handle = await start(client, queue)
        await wait_for(handle, lambda s: s.stop_reason == "approval")
        await handle.signal(
            EmployeeRun.approve, ApprovalAnswer(approved=False, by="maria", note="not yet")
        )
        outcome = await handle.result()

    assert outcome.state.status == "completed"
    assert world.sent == []
    assert any("declined by maria: not yet" in r.content for r in tool_results(outcome))
    last_prompt = provider.requests[-1].messages[-1]
    assert "declined by maria" in str(last_prompt.content)


async def test_human_reply_unsticks_a_loop(client: Client) -> None:
    def model(req: CompletionRequest) -> Any:
        said = req.messages[-1].text
        return "Order 42 ships Tuesday." if "Stop looking" in said else LOOKUP

    world = World()
    async with running_workers(client, world, fake(model, loop=True)) as queue:
        handle = await start(client, queue)
        stuck = await wait_for(handle, lambda s: s.status == "waiting_human")
        assert stuck.stop_reason == "loop"
        await handle.signal(EmployeeRun.human_reply, "Stop looking it up and answer.")
        outcome = await handle.result()
    assert (outcome.state.status, outcome.state.output) == ("completed", "Order 42 ships Tuesday.")


async def test_pause_holds_the_run_until_resume(client: Client) -> None:
    world = World()
    provider = fake(LOOKUP, "It ships Tuesday.")
    async with running_workers(client, world, provider) as queue:
        # Paused before the first step: the signal is delivered with the start.
        handle = await start(client, queue, start_signal="pause")
        await wait_for(handle, lambda s: s.step == 0)
        assert await handle.query(EmployeeRun.paused)
        assert provider.requests == [] and world.keys == []

        await handle.signal(EmployeeRun.resume)
        outcome = await handle.result()
    assert outcome.state.status == "completed" and len(world.keys) == 1


async def test_cancel_while_waiting_runs_nothing(client: Client) -> None:
    world = World()
    async with running_workers(client, world, fake(EMAIL)) as queue:
        handle = await start(client, queue)
        await wait_for(handle, lambda s: s.stop_reason == "approval")
        await handle.signal(EmployeeRun.cancel, "customer withdrew the request")
        outcome = await handle.result()
    state = outcome.state
    assert (state.status, state.stop_reason, state.output) == (
        "failed",
        "cancelled",
        "customer withdrew the request",
    )
    assert world.sent == [] and state.pending == []


async def test_unanswered_approval_expires_as_declined(client: Client) -> None:
    world = World()
    provider = fake(EMAIL, "Nobody approved, so I did not email Ana.")
    async with running_workers(client, world, provider) as queue:
        handle = await start(client, queue, wait_timeout_s=1)
        outcome = await handle.result()
    assert outcome.state.status == "completed"
    assert world.sent == []
    assert any("declined by expiry" in r.content for r in tool_results(outcome))
