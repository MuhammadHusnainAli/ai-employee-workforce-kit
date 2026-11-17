"""EmployeeRun: one durable agent run (runtime-flow.md, diagram page 2).

Every iteration is rebuilt from AgentState, which lives in workflow history. Stages that touch
the outside world are activities; `observe` is pure and runs here, in the workflow. A worker
that dies mid-run loses nothing: another worker replays the history and carries on from the
last completed activity.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from agent_harness.types import ModelResponse

    from agent_runtime.harness import observe as obs
    from agent_runtime.harness.context import BuiltContext
    from agent_runtime.harness.state import ActionResult, AgentState, Limits, ToolCalls
    from agent_runtime.workflows.types import (
        TASK_QUEUE_LLM,
        TASK_QUEUE_RUNS,
        TASK_QUEUE_TOOLS,
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


def _budget_error(exc: ActivityError) -> str | None:
    cause = exc.cause
    if isinstance(cause, ApplicationError) and cause.type == "BudgetWouldExceed":
        return cause.message
    return None


@workflow.defn(name="EmployeeRun")
class EmployeeRun:
    def __init__(self) -> None:
        self._state: AgentState | None = None

    @workflow.query
    def state(self) -> AgentState | None:
        return self._state

    @workflow.run
    async def run(self, inp: RunInput) -> RunOutcome:
        info: EmployeeInfo = await workflow.execute_activity(
            "load_employee",
            inp.employee_id,
            result_type=EmployeeInfo,
            task_queue=_queue(TASK_QUEUE_RUNS),
            start_to_close_timeout=timedelta(seconds=10),
        )
        state = AgentState.start(
            run_id=inp.run_id, tenant_id=inp.tenant_id, employee_id=inp.employee_id, task=inp.task
        )
        self._state = state
        while state.status == "running":
            state = await self._step(info, state)
            self._state = state
        return RunOutcome(state=state)

    async def _step(self, info: EmployeeInfo, state: AgentState) -> AgentState:
        limits: Limits = info.limits
        built: BuiltContext = await workflow.execute_activity(
            "build_context",
            StepInput(employee_id=info.employee_id, limits=limits, state=state),
            result_type=BuiltContext,
            task_queue=_queue(TASK_QUEUE_RUNS),
            start_to_close_timeout=timedelta(seconds=20),
        )
        try:
            response: ModelResponse = await workflow.execute_activity(
                "call_model",
                ModelInput(
                    employee_id=info.employee_id, limits=limits, request=built.request, state=state
                ),
                result_type=ModelResponse,
                task_queue=_queue(TASK_QUEUE_LLM),
                start_to_close_timeout=timedelta(seconds=180),
            )
        except ActivityError as exc:
            reason = _budget_error(exc)
            if reason is None:
                raise
            return obs.budget_stop(state, reason)

        envelope: DecisionEnvelope = await workflow.execute_activity(
            "decide",
            DecideInput(
                employee_id=info.employee_id, limits=limits, state=state, response=response
            ),
            result_type=DecisionEnvelope,
            task_queue=_queue(TASK_QUEUE_RUNS),
            start_to_close_timeout=timedelta(seconds=10),
        )
        decision = envelope.decision
        results: list[ActionResult] | None = None
        if isinstance(decision, ToolCalls):
            results = await workflow.execute_activity(
                "act",
                ActInput(
                    employee_id=info.employee_id, limits=limits, state=state, calls=decision.calls
                ),
                result_type=list[ActionResult],
                task_queue=_queue(TASK_QUEUE_TOOLS),
                start_to_close_timeout=timedelta(seconds=120),
            )
        return obs.observe(
            state,
            limits,
            response=response,
            decision=decision,
            results=results,
            history=built.messages if built.compacted else None,
        )
