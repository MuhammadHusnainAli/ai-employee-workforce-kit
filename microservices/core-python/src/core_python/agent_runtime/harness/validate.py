"""Stage 3: parse, validate, decide (agent-harness.md).

Turns one model response into exactly one Decision: Final, ToolCalls, AskHuman or GiveUp,
or an internal Repair that sends the problem back to the model (at most `max_repairs` times
per step). Deterministic checks only: no I/O, no model calls, no clock.

Order of checks for tool calls: known and granted -> not denied -> arguments valid
(Tool.validate) -> not looping -> approval gate. For a final answer: not empty -> not cut
off -> output guardrails (Guardrails.check: secrets redacted, private keys blocked).
"""

from __future__ import annotations

import hashlib
import json

from agent_harness.errors import GuardrailTripped, ToolError
from agent_harness.types import ModelResponse, ToolResultBlock, ToolUseBlock

from core_python.agent_runtime.harness.state import (
    AgentState,
    AskHuman,
    Decision,
    EmployeeSpec,
    Final,
    GiveUp,
    Repair,
    ToolCalls,
)

NOT_RUN = "Not run: another call in this step was invalid. Send the corrected calls again."


def call_signature(call: ToolUseBlock) -> str:
    """Stable identity of a call: the tool and its arguments. Same call, same signature."""
    args = json.dumps(call.input, sort_keys=True, separators=(",", ":"), default=str)
    return f"{call.name}:{hashlib.sha256(args.encode()).hexdigest()[:16]}"


def _call_problem(spec: EmployeeSpec, call: ToolUseBlock) -> str | None:
    if call.name not in spec.tools:
        granted = ", ".join(spec.tools.names) or "none"
        return f"Unknown tool {call.name!r}. Tools you can call: {granted}."
    tool = spec.tools.get(call.name)
    if tool.permission == "deny":
        return f"{call.name} is not permitted for you. Do not call it again."
    try:
        tool.validate(call.input)
    except ToolError as exc:
        return f"{exc}. Fix the arguments and call it again."
    return None


def _decide_calls(spec: EmployeeSpec, state: AgentState, calls: list[ToolUseBlock]) -> Decision:
    limits = spec.limits
    problems = {c.id: p for c in calls if (p := _call_problem(spec, c)) is not None}
    if problems:
        if state.repairs < limits.max_repairs:
            return Repair(
                results=[
                    ToolResultBlock(
                        tool_use_id=c.id, content=problems.get(c.id, NOT_RUN), is_error=True
                    )
                    for c in calls
                ]
            )
        return GiveUp(
            reason=f"tool calls still invalid after {limits.max_repairs} repairs: "
            + "; ".join(problems.values())
        )

    for c in calls:
        if state.call_counts.get(call_signature(c), 0) + 1 >= limits.loop_threshold:
            return AskHuman(
                reason=f"loop detected: {c.name} with the same arguments "
                f"{limits.loop_threshold} times",
                stop_reason="loop",
            )

    gated = [c.name for c in calls if spec.needs_approval(spec.tools.get(c.name))]
    if gated:
        # The whole step waits, so a later call never runs before an earlier one is decided.
        return AskHuman(reason="approval needed for " + ", ".join(gated), calls=calls)
    return ToolCalls(calls=calls)


def _decide_answer(spec: EmployeeSpec, state: AgentState, response: ModelResponse) -> Decision:
    can_repair = state.repairs < spec.limits.max_repairs
    text = response.text.strip()
    if not text:
        if can_repair:
            return Repair(note="Your reply was empty. Answer the task, or call a tool.")
        return GiveUp(reason="the model kept returning empty replies")
    if response.stop_reason == "max_tokens":
        if can_repair:
            return Repair(note="Your answer was cut off. Give a shorter, complete answer.")
        return GiveUp(reason="the answer kept exceeding the output limit")
    try:
        checked = spec.guardrails.check(text, where="output")
    except GuardrailTripped as exc:
        if can_repair:
            return Repair(
                note=f"Your answer was blocked by a safety check ({exc.rule}). "
                "Write it again without that content."
            )
        return AskHuman(reason=str(exc), stop_reason="guardrail")
    return Final(text=checked)


def decide(spec: EmployeeSpec, state: AgentState, response: ModelResponse) -> Decision:
    calls = response.tool_uses
    if calls:
        return _decide_calls(spec, state, calls)
    return _decide_answer(spec, state, response)
