"""task.* events start and steer EmployeeRun (just up). The handler is called as the inbox does."""

import uuid
from typing import Any

import pytest
from agent_harness.types import ToolUseBlock
from temporalio.client import Client

from wfk_platform.db import Database
from wfk_platform.outbox import CloudEvent, new_event

from core_python.agent_runtime.adapters.tasks import TaskCommands
from core_python.agent_runtime.workflows.employee_run import EmployeeRun
from core_python.agent_runtime.workflows.types import RunOutcome

from .conftest import TENANT, World, fake, running_workers, wait_for

pytestmark = pytest.mark.integration

LOOKUP = ToolUseBlock(id="c1", name="lookup", input={"order": 42})
EMAIL = ToolUseBlock(id="c2", name="send_email", input={"to": "ana@example.com", "body": "Tue"})
SOURCE = "//cell-local/task-service"


def task_event(event_type: str, task_id: str, **data: Any) -> CloudEvent:
    return new_event(event_type, SOURCE, TENANT, {"task_id": task_id, **data}, subject=task_id)


def created(task_id: str) -> CloudEvent:
    return task_event(
        "task.created", task_id, employee_id="sam", input="Tell Ana where 42 is.", priority=0
    )


async def handle(commands: TaskCommands, db: Database, event: CloudEvent) -> None:
    async with db.tenant_tx(TENANT) as conn:
        await commands(conn, event)


async def test_task_created_starts_the_run_once_and_approval_finishes_it(
    client: Client, run_db: Database
) -> None:
    world = World()
    async with running_workers(client, world, fake(LOOKUP, EMAIL, "Done.")) as queue:
        commands = TaskCommands(client, queue)
        task_id = str(uuid.uuid4())
        await handle(commands, run_db, created(task_id))
        await handle(commands, run_db, created(task_id))  # redelivered: still one run

        run = client.get_workflow_handle(task_id, result_type=RunOutcome)
        waiting = await wait_for(run, lambda s: s.status == "waiting_human")
        assert waiting.stop_reason == "approval"

        decided = task_event("task.approval_decided", task_id, approved=True, by="maria", note="")
        await handle(commands, run_db, decided)
        await handle(commands, run_db, decided)  # a repeated signal changes nothing
        outcome = await run.result()

        # After the run finished: a late task.created never runs it again, a signal is dropped.
        await handle(commands, run_db, created(task_id))
        await handle(commands, run_db, task_event("task.cancel_requested", task_id, reason="x"))

    assert outcome.state.status == "completed"
    assert world.sent == ["ana@example.com"], "the email went out exactly once"
    runs = [r async for r in client.list_workflows(f"WorkflowId = '{task_id}'")]
    assert len(runs) == 1


async def test_cancel_requested_cancels_a_waiting_run(client: Client, run_db: Database) -> None:
    world = World()
    async with running_workers(client, world, fake(LOOKUP, EMAIL, "Done.")) as queue:
        commands = TaskCommands(client, queue)
        task_id = str(uuid.uuid4())
        await handle(commands, run_db, created(task_id))
        run = client.get_workflow_handle(task_id, result_type=RunOutcome)
        await wait_for(run, lambda s: s.status == "waiting_human")
        await handle(commands, run_db, task_event("task.cancel_requested", task_id, reason="no"))
        outcome = await run.result()
    assert (outcome.state.status, outcome.state.stop_reason) == ("failed", "cancelled")
    assert world.sent == []


async def test_reply_given_reaches_the_run(client: Client, run_db: Database) -> None:
    world = World()
    async with running_workers(client, world, fake(LOOKUP, EMAIL, "Done.")) as queue:
        commands = TaskCommands(client, queue)
        task_id = str(uuid.uuid4())
        await handle(commands, run_db, created(task_id))
        run = client.get_workflow_handle(task_id, result_type=RunOutcome)
        await wait_for(run, lambda s: s.status == "waiting_human")
        await handle(commands, run_db, task_event("task.reply_given", task_id, text="hello"))
        history = await run.fetch_history()
        await run.signal(EmployeeRun.cancel, "test over")
        await run.result()
    signals = [
        e.workflow_execution_signaled_event_attributes.signal_name
        for e in history.events
        if e.HasField("workflow_execution_signaled_event_attributes")
    ]
    assert signals == ["human_reply"]


async def test_signals_for_runs_that_never_started_are_dropped(
    client: Client, run_db: Database
) -> None:
    commands = TaskCommands(client, "unused-queue")
    missing = str(uuid.uuid4())
    for event in (
        task_event("task.cancel_requested", missing, reason="x"),
        task_event("task.reply_given", missing, text="hi"),
        task_event("task.approval_decided", missing, approved=False, by="x"),
        task_event("task.state_changed", missing, to="running"),
    ):
        await handle(commands, run_db, event)  # no error, nothing to do
