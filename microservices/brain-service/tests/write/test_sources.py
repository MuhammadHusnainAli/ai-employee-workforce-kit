"""Which run events the brain learns from."""

import uuid
from datetime import UTC, datetime
from typing import Any

from brain_service.domain.write.sources import from_run_event

TENANT = uuid.UUID("0193a7c4-0000-7000-8000-000000000001")
NOW = datetime.now(UTC)


def ev(event_type: str, **data: Any):
    base = {"run_id": "r1", "employee_id": "sam", "task": "Do it.", "output": "Done."}
    return from_run_event(event_type, uuid.uuid4(), TENANT, NOW, base | data)


def test_completed_run_is_a_success() -> None:
    s = ev("run.completed", stop_reason="final")
    assert s is not None
    assert (s.outcome, s.task, s.output, s.run_id) == ("success", "Do it.", "Done.", "r1")


def test_failed_runs_are_failures_or_escalations() -> None:
    error = ev("run.failed", stop_reason="error")
    budget = ev("run.failed", stop_reason="budget")
    assert error is not None and error.outcome == "failure"
    assert budget is not None and budget.outcome == "escalated"


def test_nothing_to_learn() -> None:
    assert ev("run.failed", stop_reason="cancelled") is None, "a person stopped it"
    assert ev("run.step_completed") is None
    assert ev("run.started") is None
    assert ev("run.completed", task="") is None, "older events without the task"
    assert ev("run.completed", employee_id="") is None
