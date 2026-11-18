"""Which events the brain learns from, and how each becomes a `Source`. Pure: no I/O."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from brain_service.domain.write.model import Source

#: Run events with an outcome. Steps in between carry no task or output.
RUN_EVENTS = ("run.completed", "run.failed")


def from_run_event(
    event_type: str,
    event_id: uuid.UUID,
    tenant_id: uuid.UUID,
    occurred_at: datetime,
    data: dict[str, Any],
) -> Source | None:
    """The source for a finished run, or None when there is nothing to learn from it."""
    if event_type not in RUN_EVENTS:
        return None
    employee_id = str(data.get("employee_id") or "")
    task = str(data.get("task") or "")
    if not employee_id or not task:
        return None
    stop_reason = str(data.get("stop_reason") or "")
    if stop_reason == "cancelled":
        return None  # someone stopped it: no outcome to learn from
    if event_type == "run.completed":
        outcome = "success"
    elif stop_reason in ("approval", "loop", "budget", "guardrail"):
        outcome = "escalated"
    else:
        outcome = "failure"
    return Source(
        event_id=event_id,
        event_type=event_type,
        tenant_id=tenant_id,
        employee_id=employee_id,
        run_id=str(data.get("run_id") or "") or None,
        occurred_at=occurred_at,
        task=task,
        output=str(data.get("output") or ""),
        outcome=outcome,
        stop_reason=stop_reason,
    )
