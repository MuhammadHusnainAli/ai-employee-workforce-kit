from agent_harness import tool
from agent_harness.types import Message, ModelResponse, ToolUseBlock

from agent_runtime.harness.state import (
    AgentState,
    AskHuman,
    EmployeeSpec,
    Final,
    GiveUp,
    Repair,
    ToolCalls,
)
from agent_runtime.harness.validate import NOT_RUN, call_signature, decide


@tool
def lookup(order: int) -> str:
    """Look an order up.

    Args:
        order: the order number.
    """
    return "shipped"


@tool
def send_email(to: str, body: str) -> str:
    """Send an email.

    Args:
        to: recipient.
        body: the text.
    """
    return "sent"


@tool(permission="deny")
def drop_table() -> str:
    """Never allowed."""
    return ""


def spec() -> EmployeeSpec:
    return EmployeeSpec.build(
        name="Sam",
        instructions="",
        model="fake-1",
        tools=[lookup, send_email, drop_table],
        tiers={"lookup": "R0", "send_email": "R2"},
    )


def state(repairs: int = 0) -> AgentState:
    s = AgentState.start(run_id="r", tenant_id="t", employee_id="e", task="x")
    s.repairs = repairs
    return s


def calls(*blocks: ToolUseBlock) -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=list(blocks)), stop_reason="tool_use"
    )


def text(t: str, stop: str = "end_turn") -> ModelResponse:
    return ModelResponse(message=Message.assistant(t), stop_reason=stop)  # type: ignore[arg-type]


def use(name: str, id: str = "c1", **args: object) -> ToolUseBlock:
    return ToolUseBlock(id=id, name=name, input=dict(args))


# ---- final answers -----------------------------------------------------------------------


def test_plain_answer_is_final() -> None:
    assert decide(spec(), state(), text("It shipped.")) == Final(text="It shipped.")


def test_secret_in_answer_is_redacted() -> None:
    d = decide(spec(), state(), text("Use key sk-ant-abcdefghijklmnopqrstuvwxyz123"))
    assert isinstance(d, Final)
    assert "sk-ant-abcdefghijklmnop" not in d.text


def test_blocked_answer_is_repaired_then_escalated() -> None:
    leak = "here: -----BEGIN RSA PRIVATE KEY-----"
    first = decide(spec(), state(), text(leak))
    assert isinstance(first, Repair) and "private_key" in first.note
    last = decide(spec(), state(repairs=2), text(leak))
    assert isinstance(last, AskHuman) and last.stop_reason == "guardrail"


def test_empty_answer_is_repaired_then_given_up() -> None:
    assert isinstance(decide(spec(), state(), text("  ")), Repair)
    assert isinstance(decide(spec(), state(repairs=2), text("")), GiveUp)


def test_cut_off_answer_is_repaired() -> None:
    d = decide(spec(), state(), text("The order is", stop="max_tokens"))
    assert isinstance(d, Repair) and "cut off" in d.note


# ---- tool calls --------------------------------------------------------------------------


def test_valid_read_runs_without_approval() -> None:
    d = decide(spec(), state(), calls(use("lookup", order=42)))
    assert isinstance(d, ToolCalls) and [c.name for c in d.calls] == ["lookup"]


def test_unknown_tool_is_repaired_and_every_call_is_answered() -> None:
    d = decide(spec(), state(), calls(use("lookup", "a", order=1), use("hack", "b")))
    assert isinstance(d, Repair)
    by_id = {r.tool_use_id: r for r in d.results}
    assert set(by_id) == {"a", "b"}, "providers reject an unanswered call"
    assert "Unknown tool 'hack'" in by_id["b"].content
    assert by_id["a"].content == NOT_RUN
    assert all(r.is_error for r in d.results)


def test_invalid_arguments_are_repaired() -> None:
    d = decide(spec(), state(), calls(use("lookup", order="not a number")))
    assert isinstance(d, Repair)
    assert "invalid arguments for lookup" in d.results[0].content


def test_denied_tool_is_refused() -> None:
    d = decide(spec(), state(), calls(use("drop_table")))
    assert isinstance(d, Repair) and "not permitted" in d.results[0].content


def test_repairs_are_bounded() -> None:
    d = decide(spec(), state(repairs=2), calls(use("hack")))
    assert isinstance(d, GiveUp) and "after 2 repairs" in d.reason


def test_r2_call_waits_for_approval_with_its_whole_step() -> None:
    d = decide(
        spec(),
        state(),
        calls(use("lookup", "a", order=1), use("send_email", "b", to="x", body="y")),
    )
    assert isinstance(d, AskHuman)
    assert d.stop_reason == "approval"
    assert "send_email" in d.reason
    assert [c.id for c in d.calls] == ["a", "b"]


def test_allowlisted_r2_call_runs() -> None:
    s = spec()
    s.auto_approve.add("send_email")
    d = decide(s, state(), calls(use("send_email", to="x", body="y")))
    assert isinstance(d, ToolCalls)


def test_same_call_three_times_is_a_loop() -> None:
    call = use("lookup", order=42)
    st = state()
    st.call_counts[call_signature(call)] = 2
    d = decide(spec(), st, calls(call))
    assert isinstance(d, AskHuman) and d.stop_reason == "loop"


def test_signature_ignores_argument_order_and_call_id() -> None:
    a = ToolUseBlock(id="1", name="t", input={"x": 1, "y": 2})
    b = ToolUseBlock(id="2", name="t", input={"y": 2, "x": 1})
    assert call_signature(a) == call_signature(b)
    assert call_signature(a) != call_signature(ToolUseBlock(name="t", input={"x": 2, "y": 2}))
