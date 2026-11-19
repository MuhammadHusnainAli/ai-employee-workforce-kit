"""Suites, regression sets and the verdict."""

from pathlib import Path

from agent_harness.evaluation import EvalReport, TaskOutcome

from core_python.evaluation.adapters.agent import result
from core_python.evaluation.domain import suites

SUITES = Path(__file__).resolve().parents[2] / "suites"


def report(label: str, *outcomes: tuple[str, bool, list[str]]) -> EvalReport:
    return EvalReport(
        label=label,
        outcomes=[
            TaskOutcome(id=i, passed=ok, score=1.0 if ok else 0.0, tags=tags)
            for i, ok, tags in outcomes
        ],
    )


def test_the_shipped_suites_load() -> None:
    tasks = suites.suite(SUITES, "summariser")
    safety = [t for t in tasks if suites.SAFETY in t.tags]
    assert len(tasks) > len(safety) >= 3, "golden tasks plus every safety task"
    assert all(t.expect.no_error for t in tasks)


def test_regression_sets_come_from_past_successes() -> None:
    episodes = [
        "Task: Book the review with Ana.\nOutcome: success (final).\nResult: done",
        "Task: Pay the invoice.\nOutcome: failure (error).",
        "Task: Book the review with Ana.\nOutcome: success (final).",
        "not an episode",
    ]
    tasks = suites.regression(episodes)
    assert [(t.input, t.tags) for t in tasks] == [("Book the review with Ana.", ["regression"])]


def test_verdict() -> None:
    ok = [("g1", True, []), ("s1", True, ["safety"])]
    assert suites.verdict(report("b", *ok), report("c", *ok)) == (True, [])

    passed, why = suites.verdict(report("b", *ok), report("c", ("g1", False, []), ok[1]))
    assert not passed and why[0] == "regressed: g1"

    unsafe = report("c", ok[0], ("s1", False, ["safety"]))
    passed, why = suites.verdict(report("b", ok[0], ("s1", False, ["safety"])), unsafe)
    assert not passed and why == ["failed safety tasks: s1"], "unsafe even if it was before"

    better = report("c", ("g1", True, []), ("g2", True, []))
    assert suites.verdict(report("b", ("g1", True, []), ("g2", False, [])), better)[0]
    assert suites.verdict(report("b"), report("c")) == (False, ["no tasks ran"])


def test_run_outcomes_become_harness_results() -> None:
    done = result({"state": {"status": "completed", "output": "Hi", "step": 2}})
    assert (done.output, done.steps, done.stop_reason, done.error) == ("Hi", 2, "end_turn", None)
    failed = result({"state": {"status": "failed", "stop_reason": "budget"}})
    assert (failed.stop_reason, failed.error) == ("error", "budget")
