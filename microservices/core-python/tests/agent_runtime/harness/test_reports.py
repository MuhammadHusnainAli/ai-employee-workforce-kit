"""Reports from handed-down tasks, as the run's model sees them. Pure."""

from agent_harness.types import Message, ModelResponse, Usage

from core_python.agent_runtime.harness.observe import reports_back
from core_python.agent_runtime.harness.reports import ChildReport, render_reports
from core_python.agent_runtime.harness.state import AgentState, Limits


def child(
    eid: str, status: str = "completed", output: str = "done", reason: str = "final"
) -> ChildReport:
    return ChildReport(
        child_task_id=f"t-{eid}",
        employee_id=eid,
        title=f"Work for {eid}",
        status=status,
        stop_reason=reason,
        output=output,
    )


def test_reports_name_who_did_what_and_how_it_ended() -> None:
    text = render_reports(
        [child("sales-head", output="3 demos booked"), child("marketing", "failed", "", "error")],
        still_open=0,
    )
    assert "sales-head" in text and "3 demos booked" in text and "Work for sales-head" in text
    assert "marketing" in text and "failed" in text and "error" in text
    assert "still working" not in text


def test_reports_say_who_is_still_working() -> None:
    text = render_reports([child("a")], still_open=2)
    assert "2" in text and "still working" in text


def test_nothing_new_to_report() -> None:
    assert "no new reports" in render_reports([], still_open=0).lower()


def test_reports_back_keeps_the_run_going_with_the_reports() -> None:
    state = AgentState.start(run_id="r", tenant_id="t", employee_id="e", task="goal")
    response = ModelResponse(message=Message.assistant("All done."), usage=Usage(cost_usd=0.01))
    new = reports_back(
        state,
        Limits(max_steps=10),
        response=response,
        final_text="All done.",
        report="sales-head: 3 demos booked",
    )
    assert new.status == "running" and new.output is None and new.stop_reason == ""
    assert new.step == state.step + 1
    assert [m.role for m in new.messages[-2:]] == ["assistant", "user"]
    assert "3 demos booked" in new.messages[-1].text
    assert new.usage.cost_usd == 0.01


def test_reports_back_still_stops_at_the_step_limit() -> None:
    state = AgentState.start(run_id="r", tenant_id="t", employee_id="e", task="goal")
    state = state.model_copy(update={"step": 9})
    response = ModelResponse(message=Message.assistant("x"), usage=Usage())
    new = reports_back(state, Limits(max_steps=10), response=response, final_text="x", report="r")
    assert (new.status, new.stop_reason) == ("failed", "max_steps")
