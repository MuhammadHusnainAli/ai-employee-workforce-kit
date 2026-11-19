"""The employee, as an agent the harness `Evaluator` can run.

Each `run(task)` is a real EmployeeRun in agent-runtime, started by name (no code shared) on the
evaluation queue, in mode `eval`, optionally with a candidate brain change. Eval runs are
recorded like any run but marked, so nothing learns from them or answers anyone with them.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from agent_harness.types import RunResult
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

#: How long an eval run may wait for a person before the run gives up waiting.
WAIT_FOR_PERSON_S = 30


def result(outcome: dict[str, Any]) -> RunResult:
    """agent-runtime's RunOutcome as the harness's RunResult."""
    state: dict[str, Any] = outcome.get("state") or {}
    status = state.get("status", "")
    failed = status == "failed"
    stop = "error" if failed else ("approval" if status == "waiting_human" else "end_turn")
    return RunResult(
        output=state.get("output") or "",
        steps=int(state.get("step") or 0),
        stop_reason=stop,  # type: ignore[arg-type]
        error=(state.get("stop_reason") or "failed") if failed else None,
        agent=str(state.get("employee_id") or ""),
    )


class EmployeeAgent:
    def __init__(
        self,
        client: Client,
        *,
        tenant_id: str,
        employee_id: str,
        queue: str,
        candidate: str = "",
        timeout_s: float = 300,
        run_prefix: str = "eval",
    ) -> None:
        self.client = client
        self.tenant_id = tenant_id
        self.employee_id = employee_id
        self.queue = queue
        self.candidate = candidate
        self.timeout = timedelta(seconds=timeout_s)
        self.prefix = run_prefix

    async def run(self, task: str, **_: Any) -> RunResult:
        run_id = f"{self.prefix}-{uuid.uuid4()}"
        inp = {
            "run_id": run_id,
            "tenant_id": self.tenant_id,
            "employee_id": self.employee_id,
            "task": task,
            "mode": "eval",
            "candidate": self.candidate,
            "wait_timeout_s": WAIT_FOR_PERSON_S,
        }
        try:
            handle = await self.client.start_workflow(
                "EmployeeRun",
                inp,
                id=run_id,
                task_queue=self.queue,
                execution_timeout=self.timeout,
                result_type=dict,
            )
        except WorkflowAlreadyStartedError:  # pragma: no cover - uuid4 ids
            handle = self.client.get_workflow_handle(run_id, result_type=dict)
        return result(await handle.result())
