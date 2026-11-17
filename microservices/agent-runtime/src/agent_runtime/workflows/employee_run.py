"""EmployeeRun: one durable agent run (runtime-flow.md, diagram page 2).

Every iteration is rebuilt from AgentState, which lives in workflow history. Stages that touch
the outside world are activities, each called under its contract (timeout, heartbeat, retry
policy); `observe` is pure and runs here, in the workflow. A worker that dies mid-run loses
nothing: another worker replays the history and carries on from the last completed activity.
"""

from __future__ import annotations

from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from agent_harness.types import ModelResponse

    from agent_runtime.activities.contract import CONTRACTS
    from agent_runtime.harness import observe as obs
    from agent_runtime.harness.context import BuiltContext
    from agent_runtime.harness.state import ActionResult, AgentState, ToolCalls
    from agent_runtime.workflows.types import (
        TASK_QUEUE_RUNS,
        ActInput,
        DecideInput,
        DecisionEnvelope,
        EmployeeInfo,
        ModelInput,
        RunInput,
        RunOutcome,
        StepInput,
    )


def _queue(name: str) -> str:
    """Activity queue next to this workflow's own: `t1-runs` sends model calls to `t1-llm`."""
    own = workflow.info().task_queue
    prefix = own[: -len(TASK_QUEUE_RUNS)] if own.endswith(TASK_QUEUE_RUNS) else ""
    return prefix + name


def _cause(exc: ActivityError) -> tuple[str, str]:
    """(error type, message) of the failure behind an ActivityError."""
    cause = exc.cause
    if isinstance(cause, ApplicationError):
        return cause.type or "ApplicationError", cause.message
    return (type(cause).__name__ if cause else "ActivityError"), str(cause or exc)


async def _call(name: str, arg: Any, result_type: Any) -> Any:
    """Run an activity under its contract. There is no activity call without one."""
    c = CONTRACTS[name]
    return await workflow.execute_activity(
        name,
        arg,
        result_type=result_type,
        task_queue=_queue(c.queue),
        start_to_close_timeout=c.start_to_close,
        schedule_to_close_timeout=c.schedule_to_close,
        heartbeat_timeout=c.heartbeat,
        retry_policy=c.retry,
    )


@workflow.defn(name="EmployeeRun")
class EmployeeRun:
    def __init__(self) -> None:
        self._state: AgentState | None = None

    @workflow.query
    def state(self) -> AgentState | None:
        return self._state

    @workflow.run
    async def run(self, inp: RunInput) -> RunOutcome:
        state = AgentState.start(
            run_id=inp.run_id, tenant_id=inp.tenant_id, employee_id=inp.employee_id, task=inp.task
        )
        self._state = state
        try:
            info: EmployeeInfo = await _call("load_employee", inp.employee_id, EmployeeInfo)
        except ActivityError as exc:
            kind, message = _cause(exc)
            self._state = obs.fail(state, f"{kind}: {message}")
            return RunOutcome(state=self._state)

        while state.status == "running":
            try:
                state = await self._step(info, state)
            except ActivityError as exc:
                kind, message = _cause(exc)
                state = obs.fail(state, f"{kind}: {message}")
            self._state = state
        return RunOutcome(state=state)

    async def _step(self, info: EmployeeInfo, state: AgentState) -> AgentState:
        limits = info.limits
        built: BuiltContext = await _call(
            "build_context",
            StepInput(employee_id=info.employee_id, limits=limits, state=state),
            BuiltContext,
        )
        try:
            response: ModelResponse = await _call(
                "call_model",
                ModelInput(
                    employee_id=info.employee_id, limits=limits, request=built.request, state=state
                ),
                ModelResponse,
            )
        except ActivityError as exc:
            kind, message = _cause(exc)
            if kind != "BudgetWouldExceed":
                raise
            return obs.budget_stop(state, message)

        envelope: DecisionEnvelope = await _call(
            "decide",
            DecideInput(
                employee_id=info.employee_id, limits=limits, state=state, response=response
            ),
            DecisionEnvelope,
        )
        decision = envelope.decision
        results: list[ActionResult] | None = None
        if isinstance(decision, ToolCalls):
            results = await _call(
                "act",
                ActInput(
                    employee_id=info.employee_id, limits=limits, state=state, calls=decision.calls
                ),
                list[ActionResult],
            )
        return obs.observe(
            state,
            limits,
            response=response,
            decision=decision,
            results=results,
            history=built.messages if built.compacted else None,
        )
