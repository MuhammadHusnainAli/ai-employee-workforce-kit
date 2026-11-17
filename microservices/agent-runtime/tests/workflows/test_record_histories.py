"""Record EmployeeRun histories for the replay tests in tests/replay/.

Skipped unless WFK_RECORD_HISTORIES=1. Re-record only on purpose: a recorded history stands for
runs already in flight, and replay tests prove a code change does not break them.

    WFK_RECORD_HISTORIES=1 uv run pytest tests/workflows/test_record_histories.py
"""

import json
import os
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from agent_harness.errors import AuthenticationError
from agent_harness.types import ToolUseBlock
from temporalio.client import Client, WorkflowHandle

from agent_runtime.workflows.employee_run import EmployeeRun
from agent_runtime.workflows.types import ApprovalAnswer, RunInput, RunOutcome

from .conftest import TENANT, World, fake, running_workers, wait_for

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("WFK_RECORD_HISTORIES") != "1", reason="set WFK_RECORD_HISTORIES=1"
    ),
]

OUT = Path(__file__).resolve().parents[1] / "replay" / "histories"
LOOKUP = ToolUseBlock(id="c1", name="lookup", input={"order": 42})
BAD_LOOKUP = ToolUseBlock(id="c0", name="lookup", input={"order": "forty-two"})
EMAIL = ToolUseBlock(id="c2", name="send_email", input={"to": "ana@example.com", "body": "Tue"})

Handle = WorkflowHandle[EmployeeRun, RunOutcome]


async def approve(handle: Handle) -> None:
    await wait_for(handle, lambda s: s.stop_reason == "approval")
    await handle.signal(EmployeeRun.approve, ApprovalAnswer(approved=True, by="maria"))


async def decline(handle: Handle) -> None:
    await wait_for(handle, lambda s: s.stop_reason == "approval")
    await handle.signal(EmployeeRun.approve, ApprovalAnswer(approved=False, by="maria", note="no"))


async def cancel(handle: Handle) -> None:
    await wait_for(handle, lambda s: s.stop_reason == "approval")
    await handle.signal(EmployeeRun.cancel, "withdrawn")


def bad_key(_req: Any) -> Exception:
    return AuthenticationError("invalid api key", provider="x")


SCENARIOS: dict[str, tuple[list[Any], Callable[[Handle], Awaitable[None]] | None]] = {
    "tool_call_then_answer": ([LOOKUP, "It ships Tuesday."], None),
    "repair_then_approved_write": ([BAD_LOOKUP, LOOKUP, EMAIL, "Done: Ana knows."], approve),
    "declined_write": ([EMAIL, "Understood, no email."], decline),
    "cancelled_while_waiting": ([EMAIL], cancel),
    "model_error_fails_run": ([bad_key], None),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_record(name: str, client: Client) -> None:
    script, drive = SCENARIOS[name]
    OUT.mkdir(parents=True, exist_ok=True)
    async with running_workers(client, World(), fake(*script)) as queue:
        inp = RunInput(
            run_id=f"replay-{name}", tenant_id=TENANT, employee_id="sam", task="Tell Ana."
        )
        handle = await client.start_workflow(
            EmployeeRun.run,
            inp,
            id=f"{inp.run_id}-{os.urandom(3).hex()}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
        if drive is not None:
            await drive(handle)
        await handle.result()
        history = await handle.fetch_history()
    (OUT / f"{name}.json").write_text(
        json.dumps({"workflow_id": handle.id, "history": history.to_json_dict()}, indent=1),
        encoding="utf-8",
    )
