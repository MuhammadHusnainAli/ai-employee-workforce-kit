from agent_harness import tool
from agent_harness.context import conversation_tokens
from agent_harness.tools import Tool
from agent_harness.types import Message, TextBlock, ToolResultBlock, ToolUseBlock

from agent_runtime.harness.context import DISCLOSURE, build_context
from agent_runtime.harness.state import AgentState, EmployeeSpec, Limits


@tool
def lookup(order: int) -> str:
    """Look an order up.

    Args:
        order: the order number.
    """
    return "shipped"


def spec(*, tools: list[Tool] | None = None, limits: Limits | None = None) -> EmployeeSpec:
    return EmployeeSpec.build(
        name="Sam from Support",
        instructions="Answer order questions. Look the order up first.",
        model="fake-1",
        tools=[lookup] if tools is None else tools,
        limits=limits or Limits(),
    )


def state(task: str = "Where is order 42?") -> AgentState:
    return AgentState.start(run_id="r1", tenant_id="t1", employee_id="e1", task=task)


async def test_request_carries_identity_instructions_and_only_granted_tools() -> None:
    built = await build_context(spec(), state())
    req = built.request
    assert req.model == "fake-1"
    assert req.system is not None
    assert "Sam from Support" in req.system
    assert DISCLOSURE in req.system
    assert "Look the order up first." in req.system
    assert [t.name for t in req.tools] == ["lookup"]
    assert req.metadata == {"run_id": "r1", "step": 0, "tenant_id": "t1"}
    assert not built.compacted


async def test_no_tools_granted_means_none_offered() -> None:
    built = await build_context(spec(tools=[]), state())
    assert built.request.tools == []


async def test_interrupted_tool_call_is_answered_before_the_next_call() -> None:
    s = state()
    s.messages.append(Message(role="assistant", content=[ToolUseBlock(id="c1", name="lookup")]))
    built = await build_context(spec(), s)
    last = built.request.messages[-1]
    results = [b for b in last.content if isinstance(b, ToolResultBlock)]
    assert [r.tool_use_id for r in results] == ["c1"]


async def test_compaction_shrinks_history_and_keeps_tool_pairs_intact() -> None:
    s = state()
    for i in range(40):
        s.messages += [
            Message(
                role="assistant",
                content=[TextBlock(text=f"step {i}"), ToolUseBlock(id=f"c{i}", name="lookup")],
            ),
            Message.tool_results([ToolResultBlock(tool_use_id=f"c{i}", content="x" * 4000)]),
        ]
    before = conversation_tokens(s.messages)

    built = await build_context(spec(limits=Limits(context_tokens=5_000)), s)

    assert built.compacted
    assert conversation_tokens(built.messages) < before
    seen_calls: set[str] = set()
    for m in built.messages:
        for b in m.content:
            if isinstance(b, ToolUseBlock):
                seen_calls.add(b.id)
            if isinstance(b, ToolResultBlock):
                assert b.tool_use_id in seen_calls, "a tool result lost its tool call"
