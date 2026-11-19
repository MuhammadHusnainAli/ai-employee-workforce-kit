"""Drive the stages one step at a time.

`step` runs exactly one iteration: build_context -> call_model -> decide -> act (when the
decision is ToolCalls) -> observe. Its only input that carries over is AgentState, so a step
can run on any worker from a state read back from storage. Phase 2's EmployeeRun workflow
does the same thing with each stage as a Temporal activity; `run` is the in-process loop
used by tests and local tools.
"""

from __future__ import annotations

from agent_harness.llm_providers import Provider

from core_python.agent_runtime.harness.act import act
from core_python.agent_runtime.harness.context import build_context
from core_python.agent_runtime.harness.model import BudgetWouldExceed, call_model
from core_python.agent_runtime.harness.observe import approve, budget_stop, observe, record_approved
from core_python.agent_runtime.harness.state import AgentState, Decision, EmployeeSpec, ToolCalls
from core_python.agent_runtime.harness.validate import decide


async def step(
    spec: EmployeeSpec, provider: Provider, state: AgentState
) -> tuple[AgentState, Decision | None]:
    """Run one iteration. Returns the next state and the decision (None on a budget stop)."""
    if state.status != "running":
        raise ValueError(f"cannot step a run that is {state.status}")
    built = await build_context(spec, state)
    try:
        response = await call_model(provider, built.request, state, spec.limits)
    except BudgetWouldExceed as exc:
        return budget_stop(state, str(exc)), None
    decision = decide(spec, state, response)
    results = await act(spec, state, decision.calls) if isinstance(decision, ToolCalls) else None
    new = observe(
        state,
        spec.limits,
        response=response,
        decision=decision,
        results=results,
        history=built.messages if built.compacted else None,
    )
    return new, decision


async def run(spec: EmployeeSpec, provider: Provider, state: AgentState) -> AgentState:
    """Step until the run completes, fails or waits for a person."""
    while state.status == "running":
        state, _ = await step(spec, provider, state)
    return state


async def resume_approved(spec: EmployeeSpec, state: AgentState) -> AgentState:
    """A person approved the pending calls: run exactly those, record them, carry on."""
    running, calls = approve(state)
    results = await act(spec, running, calls)
    return record_approved(running, spec.limits, calls, results)
