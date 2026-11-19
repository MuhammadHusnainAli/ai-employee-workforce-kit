import pytest
from agent_harness.types import Message, ModelResponse, ToolResultBlock, ToolUseBlock, Usage

from core_python.agent_runtime.harness.observe import (
    WITHHELD,
    approve,
    budget_stop,
    decline,
    observe,
    record_approved,
    reply,
)
from core_python.agent_runtime.harness.state import (
    ActionResult,
    AgentState,
    AskHuman,
    Final,
    GiveUp,
    Limits,
    Repair,
    ToolCalls,
)
from core_python.agent_runtime.harness.validate import call_signature

LIMITS = Limits(max_steps=5, max_cost_usd=1.0)
CALL = ToolUseBlock(id="c1", name="lookup", input={"order": 42})


def state() -> AgentState:
    return AgentState.start(run_id="r", tenant_id="t", employee_id="e", task="Where is 42?")


def answer(text: str, cost: float = 0.01) -> ModelResponse:
    return ModelResponse(
        message=Message.assistant(text).model_copy(update={"ts": 5.0}),
        usage=Usage(input_tokens=10, output_tokens=5, cost_usd=cost, calls=1),
    )


def asks(*calls: ToolUseBlock, cost: float = 0.01) -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=list(calls), ts=5.0),
        stop_reason="tool_use",
        usage=Usage(cost_usd=cost, calls=1),
    )


def result(call: ToolUseBlock, content: str = "shipped") -> ActionResult:
    return ActionResult(call_id=call.id, name=call.name, content=content, idempotency_key="r:0:0")


def test_observe_is_pure() -> None:
    s = state()
    before = s.to_json()
    observe(s, LIMITS, response=answer("hi"), decision=Final(text="hi"))
    assert s.to_json() == before, "the input state must not change"
    a = observe(s, LIMITS, response=answer("hi"), decision=Final(text="hi"))
    b = observe(s, LIMITS, response=answer("hi"), decision=Final(text="hi"))
    assert a.to_json() == b.to_json()


def test_final_completes_and_stores_the_checked_text() -> None:
    raw = answer("key sk-ant-SECRETSECRETSECRET123")
    new = observe(state(), LIMITS, response=raw, decision=Final(text="key [REDACTED]"))
    assert (new.status, new.stop_reason, new.output) == ("completed", "final", "key [REDACTED]")
    assert "SECRET" not in new.to_json(), "the raw reply must never reach durable state"
    assert new.usage.cost_usd == pytest.approx(0.01)


def test_tool_calls_record_results_counts_and_advance() -> None:
    new = observe(
        state(),
        LIMITS,
        response=asks(CALL),
        decision=ToolCalls(calls=[CALL]),
        results=[result(CALL)],
    )
    assert new.status == "running"
    assert new.step == 1
    assert new.messages[-2].tool_uses == [CALL]
    assert isinstance(new.messages[-1].content[0], ToolResultBlock)
    assert new.call_counts == {call_signature(CALL): 1}


def test_repair_answers_bad_calls_and_counts_the_attempt() -> None:
    fix = ToolResultBlock(tool_use_id="c1", content="bad args", is_error=True)
    new = observe(state(), LIMITS, response=asks(CALL), decision=Repair(results=[fix]))
    assert new.repairs == 1 and new.step == 1
    assert new.messages[-1].content == [fix]


def test_repair_note_is_said_to_the_model() -> None:
    new = observe(state(), LIMITS, response=answer(""), decision=Repair(note="Answer, please."))
    assert new.messages[-1].role == "user" and new.messages[-1].text == "Answer, please."


def test_guardrail_stop_withholds_the_blocked_answer() -> None:
    leak = answer("-----BEGIN RSA PRIVATE KEY----- abc")
    new = observe(
        state(), LIMITS, response=leak, decision=AskHuman(reason="blocked", stop_reason="guardrail")
    )
    assert new.status == "waiting_human" and new.stop_reason == "guardrail"
    assert "PRIVATE KEY" not in new.to_json()
    assert new.messages[-1].text == WITHHELD


def test_give_up_fails_and_answers_dangling_calls() -> None:
    new = observe(state(), LIMITS, response=asks(CALL), decision=GiveUp(reason="stuck"))
    assert (new.status, new.stop_reason, new.output) == ("failed", "give_up", "stuck")
    last = new.messages[-1].content[0]
    assert isinstance(last, ToolResultBlock) and last.tool_use_id == "c1"


def test_loop_stop_answers_dangling_calls() -> None:
    new = observe(
        state(), LIMITS, response=asks(CALL), decision=AskHuman(reason="loop", stop_reason="loop")
    )
    assert new.stop_reason == "loop" and new.pending == []
    assert new.messages[-1].content[0].tool_use_id == "c1"  # type: ignore[union-attr]


def test_max_steps_and_max_cost_stop_the_run() -> None:
    s = state()
    s.step = 4
    new = observe(
        s, LIMITS, response=asks(CALL), decision=ToolCalls(calls=[CALL]), results=[result(CALL)]
    )
    assert (new.status, new.stop_reason) == ("failed", "max_steps")

    rich = observe(
        state(),
        LIMITS,
        response=asks(CALL, cost=1.5),
        decision=ToolCalls(calls=[CALL]),
        results=[result(CALL)],
    )
    assert (rich.status, rich.stop_reason) == ("failed", "max_cost")


def test_compacted_history_replaces_the_stored_one() -> None:
    short = [Message.user("summary of earlier work")]
    new = observe(state(), LIMITS, response=answer("ok"), decision=Final(text="ok"), history=short)
    assert new.messages[0].text == "summary of earlier work"
    assert len(new.messages) == 2


# ---- waiting for a person ----------------------------------------------------------------


def waiting() -> AgentState:
    return observe(
        state(),
        LIMITS,
        response=asks(CALL),
        decision=AskHuman(reason="approve refund", calls=[CALL]),
    )


def test_approval_wait_keeps_the_calls_pending() -> None:
    s = waiting()
    assert (s.status, s.stop_reason, s.pending) == ("waiting_human", "approval", [CALL])


def test_approve_then_record_runs_exactly_the_pending_calls() -> None:
    running, calls = approve(waiting())
    assert calls == [CALL] and running.status == "running" and running.pending == []
    done = record_approved(running, LIMITS, calls, [result(CALL, "refunded")])
    assert done.step == 1
    assert done.messages[-1].content[0].content == "refunded"  # type: ignore[union-attr]


def test_decline_is_an_answer_the_model_reads() -> None:
    new = decline(waiting(), LIMITS, by="maria", note="over the limit")
    assert new.status == "running" and new.pending == []
    block = new.messages[-1].content[0]
    assert isinstance(block, ToolResultBlock)
    assert block.tool_use_id == "c1" and "declined by maria: over the limit" in block.content


def test_reply_resumes_a_question_but_not_an_approval() -> None:
    stuck = budget_stop(state(), "would overspend")
    assert stuck.stop_reason == "budget"
    resumed = reply(stuck, "Budget raised, carry on.")
    assert resumed.status == "running" and resumed.messages[-1].text == "Budget raised, carry on."
    with pytest.raises(ValueError, match="approve"):
        reply(waiting(), "yes")
    with pytest.raises(ValueError, match="not waiting for an approval"):
        approve(stuck)
