"""EmployeeRun: one durable agent run (runtime-flow.md, diagram page 2).

Every iteration is rebuilt from AgentState, which lives in workflow history. Stages that touch
the outside world are activities, each called under its contract (timeout, heartbeat, retry
policy); `observe` is pure and runs here, in the workflow. A worker that dies mid-run loses
nothing: another worker replays the history and carries on from the last completed activity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from agent_harness.types import ModelResponse

    from core_python.agent_runtime.activities.contract import CONTRACTS
    from core_python.agent_runtime.harness import observe as obs
    from core_python.agent_runtime.harness.act import wrap_untrusted
    from core_python.agent_runtime.harness.context import BuiltContext
    from core_python.agent_runtime.harness.reports import (
        DELEGATE_TASK,
        WAIT_FOR_REPORTS,
        ChildReport,
        render_reports,
    )
    from core_python.agent_runtime.harness.state import ActionResult, AgentState, Final, ToolCalls
    from core_python.agent_runtime.workflows.types import (
        TASK_QUEUE_RUNS,
        ActInput,
        ApprovalAnswer,
        DecideInput,
        DecisionEnvelope,
        EmployeeInfo,
        LedgerEntry,
        LoadEmployee,
        ModelInput,
        RunInput,
        RunOutcome,
        StartRecord,
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


@dataclass
class Facts:
    """What happened in one transition, for the ledger."""

    kind: str
    decision: dict[str, Any] = field(default_factory=dict)
    results: list[ActionResult] = field(default_factory=list)
    response: ModelResponse | None = None
    prompt_hash: str | None = None
    recall_id: str | None = None
    memory_degraded: bool = False


@workflow.defn(name="EmployeeRun")
class EmployeeRun:
    """Signals record what a person said; the run loop applies it at a safe point.

    approve      yes or no for the calls waiting on an approval
    human_reply  an answer to a question (loop, guardrail, budget)
    pause        stop before the next step (a running step finishes first)
    resume       carry on after a pause
    cancel       end the run; nothing pending runs
    child_finished   a task this run handed down has finished (task.child_finished)

    Delegation: each successful delegate_task call hands a sub-task down. A wait_for_reports
    call waits, durably, until every one of them has reported (or the wait timeout passes) and
    its result becomes their reports. A final answer while sub-tasks are still open waits the
    same way, and the run carries on with the reports instead of finishing.
    """

    def __init__(self) -> None:
        self._state: AgentState | None = None
        self._seq = 0  # next ledger entry number
        self._paused = False
        self._cancel_reason: str | None = None
        self._approval: ApprovalAnswer | None = None
        self._reply: str | None = None
        self._children: dict[str, ChildReport] = {}  # in signal order, so replay is stable
        self._reported: set[str] = set()
        self._delegated = 0
        # How many hand-offs a wait had covered when it timed out: a final answer does not wait
        # for those again, the model already knows they are still working.
        self._timed_out_at = 0
        self._wait_timeout_s = 7 * 24 * 3600.0

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

    @workflow.signal
    def child_finished(self, report: ChildReport) -> None:
        self._children.setdefault(report.child_task_id, report)

    # ---- the run ---------------------------------------------------------------------------

    @workflow.run
    async def run(self, inp: RunInput) -> RunOutcome:
        state = AgentState.start(
            run_id=inp.run_id, tenant_id=inp.tenant_id, employee_id=inp.employee_id, task=inp.task
        ).model_copy(update={"mode": inp.mode, "candidate": inp.candidate})
        self._state = state
        self._wait_timeout_s = inp.wait_timeout_s
        await _call(
            "record_start",
            StartRecord(
                run_id=inp.run_id,
                tenant_id=inp.tenant_id,
                employee_id=inp.employee_id,
                task=inp.task,
                mode=inp.mode,
                candidate=inp.candidate,
            ),
            bool,
        )
        try:
            # Employees from workforce-service need the tenant; older runs sent the id alone.
            who: object = (
                LoadEmployee(employee_id=inp.employee_id, tenant_id=inp.tenant_id)
                if workflow.patched("workforce-employees-v1")
                else inp.employee_id
            )
            info: EmployeeInfo = await _call("load_employee", who, EmployeeInfo)
        except ActivityError as exc:
            kind, message = _cause(exc)
            state = obs.fail(state, f"{kind}: {message}")
            self._state = state
            await self._record(state, Facts("error", {"error": f"{kind}: {message}"}))
            return RunOutcome(state=state, ledger_entries=self._seq)
        # Every later step uses exactly this version, whatever is published meanwhile.
        state = state.model_copy(update={"employee_version": info.version})
        self._state = state

        while not state.done:
            await workflow.wait_condition(lambda: not self._paused or self._cancelled)
            facts: Facts | None
            if self._cancelled:
                reason = self._cancel_reason or "cancelled"
                state, facts = obs.cancel(state, reason), Facts("cancelled", {"reason": reason})
            else:
                try:
                    if state.status == "running":
                        state, facts = await self._step(info, state)
                    else:
                        state, facts = await self._wait_for_person(info, state, inp.wait_timeout_s)
                except ActivityError as exc:
                    kind, message = _cause(exc)
                    state = obs.fail(state, f"{kind}: {message}")
                    facts = Facts("error", {"error": f"{kind}: {message}"})
            self._state = state
            if facts is not None:
                await self._record(state, facts)
        return RunOutcome(state=state, ledger_entries=self._seq)

    @property
    def _cancelled(self) -> bool:
        return self._cancel_reason is not None

    # ---- delegation ------------------------------------------------------------------------

    @property
    def _open_children(self) -> int:
        return max(self._delegated - len(self._children), 0)

    async def _await_reports(self) -> str:
        """Wait until everything handed down has reported (or the wait times out), then the
        reports not yet given to the model, wrapped as data: they are other employees' words."""
        try:
            await workflow.wait_condition(
                lambda: self._open_children == 0 or self._cancelled,
                timeout=timedelta(seconds=self._wait_timeout_s),
            )
        except TimeoutError:
            self._timed_out_at = self._delegated
        new = [c for cid, c in self._children.items() if cid not in self._reported]
        self._reported.update(c.child_task_id for c in new)
        return wrap_untrusted("reports", render_reports(new, still_open=self._open_children))

    async def _with_reports(self, results: list[ActionResult]) -> list[ActionResult]:
        """Count what this step handed down; fill a wait_for_reports result with the reports."""
        self._delegated += sum(1 for r in results if r.name == DELEGATE_TASK and not r.is_error)
        waits = any(r.name == WAIT_FOR_REPORTS for r in results)
        if not waits or self._delegated == 0 or not workflow.patched("org-delegation-v1"):
            return results
        text = await self._await_reports()
        return [
            r.model_copy(update={"content": text}) if r.name == WAIT_FOR_REPORTS else r
            for r in results
        ]

    async def _record(self, state: AgentState, facts: Facts) -> None:
        """Write one ledger entry. Its seq makes a retried write a no-op."""
        response = facts.response
        entry = LedgerEntry(
            run_id=state.run_id,
            tenant_id=state.tenant_id,
            employee_id=state.employee_id,
            seq=self._seq,
            kind=facts.kind,
            decision=facts.decision,
            results=[r.model_dump(mode="json") for r in facts.results],
            model=response.model if response else None,
            prompt_hash=facts.prompt_hash,
            recall_id=facts.recall_id,
            memory_degraded=facts.memory_degraded,
            input_tokens=response.usage.input_tokens if response else 0,
            output_tokens=response.usage.output_tokens if response else 0,
            cost_usd=response.usage.cost_usd if response else 0.0,
            step=state.step,
            status=state.status,
            stop_reason=state.stop_reason,
            output=state.output,
            total_input_tokens=state.usage.input_tokens,
            total_output_tokens=state.usage.output_tokens,
            total_cost_usd=state.usage.cost_usd,
            mode=state.mode,  # type: ignore[arg-type]
        )
        await _call("record_step", entry, bool)
        self._seq += 1

    async def _wait_for_person(
        self, info: EmployeeInfo, state: AgentState, timeout_s: float
    ) -> tuple[AgentState, Facts | None]:
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
                new = obs.decline(
                    state, info.limits, by="expiry", note="nobody answered in time", ts=_now()
                )
                return new, Facts("declined", {"by": "expiry"})
            new = obs.fail(state, f"nobody answered within {timeout_s:.0f} s")
            return new, Facts("expired", {"waited_s": timeout_s})
        if self._cancelled:
            return state, None  # the loop applies and records the cancel

        if approval_wait:
            answer, self._approval = self._approval, None
            assert answer is not None
            if not answer.approved:
                new = obs.decline(state, info.limits, by=answer.by, note=answer.note, ts=_now())
                return new, Facts("declined", answer.model_dump())
            running, calls = obs.approve(state)
            results: list[ActionResult] = await _call(
                "act",
                ActInput(
                    employee_id=info.employee_id, limits=info.limits, state=running, calls=calls
                ),
                list[ActionResult],
            )
            new = obs.record_approved(running, info.limits, calls, results, ts=_now())
            return new, Facts("approved", answer.model_dump(), results=results)

        text, self._reply = self._reply, None
        assert text is not None
        return obs.reply(state, text, ts=_now()), Facts("reply", {"text": text})

    async def _step(self, info: EmployeeInfo, state: AgentState) -> tuple[AgentState, Facts]:
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
            facts = Facts(
                "budget",
                {"reason": message},
                prompt_hash=built.prompt_hash,
                recall_id=built.recall_id or None,
                memory_degraded=built.memory_degraded,
            )
            return obs.budget_stop(state, message), facts

        envelope: DecisionEnvelope = await _call(
            "decide",
            DecideInput(
                employee_id=info.employee_id, limits=limits, state=state, response=response
            ),
            DecisionEnvelope,
        )
        decision = envelope.decision
        results: list[ActionResult] = []
        if isinstance(decision, ToolCalls):
            results = await _call(
                "act",
                ActInput(
                    employee_id=info.employee_id, limits=limits, state=state, calls=decision.calls
                ),
                list[ActionResult],
            )
            results = await self._with_reports(results)
        elif (
            isinstance(decision, Final)
            and self._open_children
            and self._delegated > self._timed_out_at
            and workflow.patched("org-delegation-v1")
        ):
            report = await self._await_reports()
            new = obs.reports_back(
                state,
                limits,
                response=response,
                final_text=decision.text,
                report=report,
                history=built.messages if built.compacted else None,
            )
            facts = Facts(
                "reports",
                {"final_text": decision.text, "reports": report[:4000]},
                response=response,
                prompt_hash=built.prompt_hash,
                recall_id=built.recall_id or None,
                memory_degraded=built.memory_degraded,
            )
            return new, facts
        new = obs.observe(
            state,
            limits,
            response=response,
            decision=decision,
            results=results if isinstance(decision, ToolCalls) else None,
            history=built.messages if built.compacted else None,
        )
        facts = Facts(
            decision.kind,
            decision.model_dump(mode="json"),
            results=results,
            response=response,
            prompt_hash=built.prompt_hash,
            recall_id=built.recall_id or None,
            memory_degraded=built.memory_degraded,
        )
        return new, facts
