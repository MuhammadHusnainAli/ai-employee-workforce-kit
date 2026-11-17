"""Stage 1: build the model request for this step from durable state (agent-harness.md).

Uses agent-harness-adk's ContextAssembler (stable system prompt first, so prompt caching
works) and ContextCompactor (evicts fat tool results, then summarises the head, never
orphaning a tool call). Only granted tools are offered: a tool the model cannot see, it
cannot call.
"""

from __future__ import annotations

from agent_harness.context import ContextAssembler, ContextCompactor, close_open_tool_calls
from agent_harness.llm_providers import CompletionRequest
from agent_harness.types import Message
from pydantic import BaseModel

from agent_runtime.harness.state import AgentState, EmployeeSpec

DISCLOSURE = (
    "You are an AI employee, not a human. If anyone asks, say so plainly. "
    "Never pretend to be a person."
)


class BuiltContext(BaseModel):
    """What build_context hands to call_model, plus the history it settled on."""

    request: CompletionRequest
    #: The history after closing interrupted calls and compacting. The caller stores it in
    #: state when `compacted` is true, so the next step starts from the shorter history.
    messages: list[Message]
    compacted: bool = False


def identity(spec: EmployeeSpec) -> str:
    return f"You are {spec.name}.\n{DISCLOSURE}"


def last_user_text(messages: list[Message]) -> str:
    for m in reversed(messages):
        if m.role == "user" and m.text:
            return m.text
    return ""


async def build_context(spec: EmployeeSpec, state: AgentState) -> BuiltContext:
    messages = close_open_tool_calls(list(state.messages))

    compactor = ContextCompactor(max_tokens=spec.limits.context_tokens)
    compacted = compactor.should_compact(messages)
    if compacted:
        messages = await compactor.compact(messages)

    tool_names = spec.tools.names
    system = await ContextAssembler(identity(spec), instructions=spec.instructions).build(
        query=last_user_text(messages), tool_names=tool_names
    )
    # model_validate rather than the constructor: same validation, and type checkers read
    # the package's Field(None, ...) defaults correctly this way.
    request = CompletionRequest.model_validate(
        {
            "model": spec.model,
            "system": system,
            "messages": messages,
            "tools": spec.tools.schemas(),
            "max_tokens": spec.limits.max_output_tokens,
            "metadata": {"run_id": state.run_id, "step": state.step, "tenant_id": state.tenant_id},
        }
    )
    return BuiltContext(request=request, messages=messages, compacted=compacted)
