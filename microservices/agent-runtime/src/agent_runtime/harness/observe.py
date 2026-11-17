"""Stage 5: observe and stop (agent-harness.md).

Pure functions: they take the facts of a step (the model's response, the decision, the tool
results) and return the next AgentState. No I/O and no clock (timestamps come from the
response or are passed in), so in Phase 2 they run inside the Temporal workflow itself and
replay identically.

Also here: the pure transitions for a person's answer while a run waits (approve, decline,
reply) and for a budget stop.
"""

from __future__ import annotations

from agent_harness.types import Message, ModelResponse, ToolResultBlock, ToolUseBlock

from agent_runtime.harness.state import (
    ActionResult,
    AgentState,
    AskHuman,
    Decision,
    Final,
    GiveUp,
    Limits,
    Repair,
    ToolCalls,
)
from agent_runtime.harness.validate import call_signature

WITHHELD = "[answer withheld: blocked by a guardrail]"


def _answer_unrun(calls: list[ToolUseBlock], why: str, ts: float) -> Message:
    """Providers reject a tool call without a result, so calls that will not run get one."""
    return Message.tool_results(
        [ToolResultBlock(tool_use_id=c.id, content=f"Not run: {why}", is_error=True) for c in calls]
    ).model_copy(update={"ts": ts})


def _record_results(
    state: AgentState, calls: list[ToolUseBlock], results: list[ActionResult], ts: float
) -> None:
    state.messages.append(
        Message.tool_results([r.as_block() for r in results]).model_copy(update={"ts": ts})
    )
    for c in calls:
        sig = call_signature(c)
        state.call_counts[sig] = state.call_counts.get(sig, 0) + 1
    state.repairs = 0
    state.step += 1


def _apply_stop_conditions(state: AgentState, limits: Limits) -> AgentState:
    if state.status != "running":
        return state
    if state.usage.cost_usd >= limits.max_cost_usd:
        state.status, state.stop_reason = "failed", "max_cost"
    elif state.step >= limits.max_steps:
        state.status, state.stop_reason = "failed", "max_steps"
    return state


def observe(
    state: AgentState,
    limits: Limits,
    *,
    response: ModelResponse,
    decision: Decision,
    results: list[ActionResult] | None = None,
    history: list[Message] | None = None,
) -> AgentState:
    """Fold one step into the next state.

    `history` is the (possibly compacted) message list build_context used; when given it
    replaces the stored history, so compaction is kept. `results` are act's results when the
    decision was ToolCalls.
    """
    new = state.model_copy(deep=True)
    if history is not None:
        new.messages = [m.model_copy(deep=True) for m in history]
    new.usage = new.usage + response.usage
    ts = response.message.ts
    said = response.message.model_copy(deep=True)
    calls = said.tool_uses

    match decision:
        case Final(text=text):
            # Store what passed the guardrails (secrets redacted), not the raw reply.
            new.messages.append(Message.assistant(text).model_copy(update={"ts": ts}))
            new.status, new.stop_reason, new.output = "completed", "final", text

        case ToolCalls():
            new.messages.append(said)
            _record_results(new, decision.calls, results or [], ts)

        case Repair(results=repair_results, note=note):
            new.messages.append(said)
            if repair_results:
                new.messages.append(
                    Message.tool_results(repair_results).model_copy(update={"ts": ts})
                )
            if note:
                new.messages.append(Message.user(note).model_copy(update={"ts": ts}))
            new.repairs += 1
            new.step += 1

        case AskHuman(reason=reason, stop_reason=stop, calls=pending):
            if stop == "guardrail":
                new.messages.append(Message.assistant(WITHHELD).model_copy(update={"ts": ts}))
            else:
                new.messages.append(said)
                if not pending and calls:
                    new.messages.append(_answer_unrun(calls, f"stopped for review ({reason})", ts))
            new.status, new.stop_reason = "waiting_human", stop
            new.pending, new.pending_reason = list(pending), reason

        case GiveUp(reason=reason, stop_reason=stop):
            new.messages.append(said)
            if calls:
                new.messages.append(_answer_unrun(calls, "the run gave up", ts))
            new.status, new.stop_reason, new.output = "failed", stop, reason

    return _apply_stop_conditions(new, limits)


# ---- a person's answer while the run waits -----------------------------------------------


def approve(state: AgentState) -> tuple[AgentState, list[ToolUseBlock]]:
    """Approved: the pending calls run exactly as approved. Returns the calls to act on;
    feed act's results to `record_approved`."""
    if state.stop_reason != "approval" or not state.pending:
        raise ValueError("the run is not waiting for an approval")
    new = state.model_copy(deep=True)
    calls = new.pending
    new.pending, new.pending_reason = [], ""
    new.status, new.stop_reason = "running", ""
    return new, calls


def record_approved(
    state: AgentState,
    limits: Limits,
    calls: list[ToolUseBlock],
    results: list[ActionResult],
    ts: float = 0.0,
) -> AgentState:
    new = state.model_copy(deep=True)
    _record_results(new, calls, results, ts)
    return _apply_stop_conditions(new, limits)


def decline(
    state: AgentState, limits: Limits, *, by: str, note: str = "", ts: float = 0.0
) -> AgentState:
    """Declined: a no is an answer. The model reads it as the calls' result and carries on."""
    if state.stop_reason != "approval" or not state.pending:
        raise ValueError("the run is not waiting for an approval")
    new = state.model_copy(deep=True)
    why = f"declined by {by}" + (f": {note}" if note else "")
    new.messages.append(_answer_unrun(new.pending, why, ts))
    new.pending, new.pending_reason = [], ""
    new.status, new.stop_reason = "running", ""
    new.repairs = 0
    new.step += 1
    return _apply_stop_conditions(new, limits)


def reply(state: AgentState, text: str, *, ts: float = 0.0) -> AgentState:
    """A person answered a question (loop, guardrail, budget). The run continues from it."""
    if state.status != "waiting_human":
        raise ValueError("the run is not waiting for a person")
    if state.stop_reason == "approval":
        raise ValueError("this run waits for an approval: use approve() or decline()")
    new = state.model_copy(deep=True)
    new.messages.append(Message.user(text).model_copy(update={"ts": ts}))
    new.status, new.stop_reason, new.pending_reason = "running", "", ""
    new.repairs = 0
    return new


def fail(state: AgentState, reason: str) -> AgentState:
    """An activity failed for good. The run ends as failed, with the reason recorded."""
    new = state.model_copy(deep=True)
    new.status, new.stop_reason, new.output = "failed", "error", reason
    return new


def budget_stop(state: AgentState, reason: str) -> AgentState:
    """call_model refused to overspend. The run waits for a person to raise the budget."""
    new = state.model_copy(deep=True)
    new.status, new.stop_reason, new.pending_reason = "waiting_human", "budget", reason
    return new
