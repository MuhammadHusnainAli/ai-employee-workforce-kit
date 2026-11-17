"""EmployeeRun: one durable agent run (runtime-flow.md, diagram page 2).

Every iteration is rebuilt from AgentState, which lives in workflow history. Stages that touch
the outside world are activities, each called under its contract (timeout, heartbeat, retry
policy); `observe` is pure and runs here, in the workflow. A worker that dies mid-run loses
nothing: another worker replays the history and carries on from the last completed activity.
"""

from __future__ import annotations

from datetime import timedelta
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
        ApprovalAnswer,
        DecideInput,
        DecisionEnvelope,
        EmployeeInfo,
        ModelInput,
        RunInput,
        RunOutcome,
        StepInput,
    )


def _now() -> float:
    """Workflow time (deterministic on replay), for message timestamps."""
    return workflow.now().timestamp()


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
    """Signals record what a person said; the run loop applies it at a safe point.

    approve      yes or no for the calls waiting on an approval
    human_reply  an answer to a question (loop, guardrail, budget)
    pause        stop before the next step (a running step finishes first)
    resume       carry on after a pause
    cancel       end the run; nothing pending runs
    """

    def __init__(self) -> None:
        self._state: AgentState | None = None
        self._paused = False
        self._cancel_reason: str | None = None
        self._approval: ApprovalAnswer | None = None
        self._reply: str | None = None

    # ---- what people can do --------------------------------------------------------------

    @workflow.query
    def state(self) -> AgentState | None:
        return self._state

    @workflow.query
    def paused(self) -> bool:
        return self._paused

    @workflow.signal
    def approve(self, answer: ApprovalAnswer) -> None:
        self._approval = answer

    @workflow.signal
    def human_reply(self, text: str) -> None:
        self._reply = text

    @workflow.signal
    def pause(self) -> None:
        self._paused = True

    @workflow.signal
    def resume(self) -> None:
        self._paused = False

    @workflow.signal
    def cancel(self, reason: str) -> None:
        self._cancel_reason = reason or "cancelled"

    # ---- the run ---------------------------------------------------------------------------

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

        while not state.done:
            await workflow.wait_condition(lambda: not self._paused or self._cancelled)
            if self._cancelled:
                state = obs.cancel(state, self._cancel_reason or "cancelled")
            else:
                try:
                    if state.status == "running":
                        state = await self._step(info, state)
                    else:
                        state = await self._wait_for_person(info, state, inp.wait_timeout_s)
                except ActivityError as exc:
                    kind, message = _cause(exc)
                    state = obs.fail(state, f"{kind}: {message}")
            self._state = state
        return RunOutcome(state=state)

    @property
    def _cancelled(self) -> bool:
        return self._cancel_reason is not None

    async def _wait_for_person(
        self, info: EmployeeInfo, state: AgentState, timeout_s: float
    ) -> AgentState:
        """Wait durably for the answer this run needs. Other answers are ignored."""
        approval_wait = state.stop_reason == "approval"

        def answered() -> bool:
            if self._cancelled:
                return True
            return self._approval is not None if approval_wait else self._reply is not None

        try:
            await workflow.wait_condition(answered, timeout=timedelta(seconds=timeout_s))
        except TimeoutError:
            if approval_wait:  # an approval nobody gave is a no
                return obs.decline(
                    state, info.limits, by="expiry", note="nobody answered in time", ts=_now()
                )
            return obs.fail(state, f"nobody answered within {timeout_s:.0f} s")
        if self._cancelled:
            return state  # the loop applies the cancel

        if approval_wait:
            answer, self._approval = self._approval, None
            assert answer is not None
            if not answer.approved:
                return obs.decline(state, info.limits, by=answer.by, note=answer.note, ts=_now())
            running, calls = obs.approve(state)
            results: list[ActionResult] = await _call(
                "act",
                ActInput(
                    employee_id=info.employee_id, limits=info.limits, state=running, calls=calls
                ),
                list[ActionResult],
            )
            return obs.record_approved(running, info.limits, calls, results, ts=_now())

        text, self._reply = self._reply, None
        assert text is not None
        return obs.reply(state, text, ts=_now())

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
