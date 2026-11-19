from agent_harness import tool
from agent_harness.types import Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from pydantic import TypeAdapter

from core_python.agent_runtime.harness.state import (
    ActionResult,
    AgentState,
    AskHuman,
    Decision,
    EmployeeSpec,
    Final,
    GiveUp,
    Repair,
    ToolCalls,
)


def test_state_round_trips_through_json() -> None:
    state = AgentState.start(run_id="r1", tenant_id="t1", employee_id="e1", task="Find order 42")
    state.messages += [
        Message(
            role="assistant",
            content=[
                TextBlock(text="Looking."),
                ToolUseBlock(id="c1", name="lookup", input={"order": 42}),
            ],
            ts=1.0,
        ),
        Message.tool_results([ToolResultBlock(tool_use_id="c1", content="shipped")]),
    ]
    state.usage = Usage(input_tokens=10, output_tokens=3, cost_usd=0.001, calls=1)
    state.call_counts = {"lookup:abc": 1}
    state.pending = [ToolUseBlock(id="c2", name="refund", input={"amount": 5})]
    state.status, state.stop_reason = "waiting_human", "approval"

    back = AgentState.from_json(state.to_json())

    assert back == state
    assert isinstance(back.messages[1].content[1], ToolUseBlock)
    assert isinstance(back.messages[2].content[0], ToolResultBlock)


def test_start_is_deterministic() -> None:
    a = AgentState.start(run_id="r", tenant_id="t", employee_id="e", task="x")
    b = AgentState.start(run_id="r", tenant_id="t", employee_id="e", task="x")
    assert a.to_json() == b.to_json()


def test_every_decision_round_trips_through_the_union() -> None:
    adapter: TypeAdapter[Decision] = TypeAdapter(Decision)
    decisions = [
        Final(text="done"),
        ToolCalls(calls=[ToolUseBlock(id="c", name="t", input={})]),
        AskHuman(reason="approve refund", calls=[ToolUseBlock(id="c", name="refund")]),
        GiveUp(reason="stuck"),
        Repair(results=[ToolResultBlock(tool_use_id="c", content="bad", is_error=True)]),
    ]
    for d in decisions:
        assert adapter.validate_json(adapter.dump_json(d)) == d


def test_action_result_becomes_a_tool_result_block() -> None:
    r = ActionResult(call_id="c", name="t", content="ok", idempotency_key="r:1:0")
    assert r.as_block() == ToolResultBlock(tool_use_id="c", content="ok")


@tool
def lookup(order: int) -> str:
    """Look an order up.

    Args:
        order: the order number.
    """
    return "shipped"


@tool(permission="ask")
def wipe() -> str:
    """Delete everything."""
    return "gone"


def test_approval_rules() -> None:
    spec = EmployeeSpec.build(
        name="support",
        instructions="",
        model="fake-1",
        tools=[lookup, wipe],
        tiers={"lookup": "R0"},
    )
    assert spec.tier("lookup") == "R0"
    assert spec.tier("unknown") == "R1"
    assert not spec.needs_approval(lookup)
    assert spec.needs_approval(wipe)  # permission="ask"

    spec.tiers["lookup"] = "R2"
    assert spec.needs_approval(lookup)
    spec.auto_approve.add("lookup")
    assert not spec.needs_approval(lookup)
    spec.tiers["lookup"] = "R3"
    assert spec.needs_approval(lookup)  # R3 always asks
