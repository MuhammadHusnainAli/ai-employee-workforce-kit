"""Replay recorded EmployeeRun histories against the current workflow code.

A history stands for a run already in flight when new code is deployed. If a change to
EmployeeRun would make such a run take a different path, replay fails here, before it fails
in production. No Temporal server is needed: activity results come from the history.

Re-record with: WFK_RECORD_HISTORIES=1 uv run pytest tests/workflows/test_record_histories.py
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio import workflow
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from core_python.agent_runtime.workflows.employee_run import EmployeeRun
from core_python.agent_runtime.workflows.types import RunInput, RunOutcome

HISTORIES = sorted((Path(__file__).parent / "histories").glob("*.json"))


def load(path: Path) -> WorkflowHistory:
    data = json.loads(path.read_text(encoding="utf-8"))
    return WorkflowHistory.from_json(data["workflow_id"], data["history"])


def test_histories_are_recorded() -> None:
    assert len(HISTORIES) >= 5, "record them: see the module docstring"


@pytest.mark.parametrize("path", HISTORIES, ids=lambda p: p.stem)
async def test_history_replays_deterministically(path: Path) -> None:
    replayer = Replayer(workflows=[EmployeeRun], data_converter=pydantic_data_converter)
    result = await replayer.replay_workflow(load(path))
    assert result.replay_failure is None


@workflow.defn(name="EmployeeRun", sandboxed=False)
class SkipsTheLedger:
    """A careless change: the same workflow name, but it no longer records the run's start."""

    @workflow.run
    async def run(self, inp: RunInput) -> RunOutcome:
        await workflow.execute_activity(
            "load_employee", inp.employee_id, start_to_close_timeout=timedelta(seconds=10)
        )
        raise RuntimeError("unreachable: replay fails before this")


async def test_an_incompatible_change_is_caught() -> None:
    replayer = Replayer(workflows=[SkipsTheLedger], data_converter=pydantic_data_converter)
    result = await replayer.replay_workflow(load(HISTORIES[0]), raise_on_replay_failure=False)
    assert result.replay_failure is not None
    assert "nondetermin" in str(result.replay_failure).lower()
