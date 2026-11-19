"""Standup rules: facts from real task status, the model's answers parsed safely. No I/O."""

from datetime import UTC, datetime, timedelta

from core_python.collaboration.domain.standup import (
    IDLE_LINE,
    MAX_NEXT_STEPS,
    Facts,
    Member,
    Report,
    TaskFact,
    facts_from,
    idle_report,
    parse_summary,
    parse_update,
    summary_prompt,
    update_prompt,
)

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)


def task(title: str, status: str, hours_ago: float, output: str = "") -> TaskFact:
    return TaskFact(
        title=title, status=status, updated_at=NOW - timedelta(hours=hours_ago), output=output
    )


def test_facts_keep_only_what_changed_since_the_last_standup() -> None:
    facts = facts_from(
        [
            task("Call Acme", "completed", 2, "Demo booked for Tuesday"),
            task("Old win", "completed", 48),
            task("Write deck", "running", 1),
            task("Send quote", "waiting_human", 3),
            task("Import leads", "failed", 5),
            task("Old failure", "failed", 72),
        ],
        since=SINCE,
    )
    assert [t.title for t in facts.done] == ["Call Acme"]
    assert [t.title for t in facts.in_progress] == ["Write deck"]
    assert [t.title for t in facts.waiting] == ["Send quote"]
    assert [t.title for t in facts.failed] == ["Import leads"]
    assert not facts.idle


def test_nothing_new_is_idle() -> None:
    assert facts_from([task("Old win", "completed", 48)], since=SINCE).idle
    assert facts_from([], since=SINCE).idle


def test_an_idle_employee_gets_the_fixed_line() -> None:
    report = idle_report(Member(employee_id="intern", name="Ivy", title="Intern", model="m"))
    assert report.idle and report.done == IDLE_LINE and report.employee_id == "intern"


def test_an_update_is_read_from_json_or_kept_whole() -> None:
    good = parse_update(
        '```json\n{"done": "Booked a demo", "next": "Send deck", "blockers": ""}\n```'
    )
    assert (good.done, good.next, good.blockers) == ("Booked a demo", "Send deck", "")
    loose = parse_update("I booked a demo and will send the deck.")
    assert loose.done == "I booked a demo and will send the deck." and loose.next == ""


def test_a_summary_keeps_known_owners_and_at_most_ten_steps() -> None:
    steps = [{"owner": "sales-head", "action": f"Step {i}"} for i in range(15)]
    steps.append({"owner": "ghost", "action": "Nobody does this"})
    steps.append({"owner": "person", "action": "Approve the budget"})
    text = (
        '{"where_we_are": "Pipeline is healthy", "where_we_go": "Close Acme this week", '
        '"next_steps": ' + str(steps).replace("'", '"') + ', "risks": ["Quote delay"]}'
    )
    summary = parse_summary(text, owners={"sales-head", "marketing"})
    assert summary.where_we_are == "Pipeline is healthy"
    assert summary.where_we_go == "Close Acme this week"
    assert len(summary.next_steps) == MAX_NEXT_STEPS == 10
    assert {s.owner for s in summary.next_steps} <= {"sales-head", "person"}
    assert summary.risks == ["Quote delay"]


def test_a_summary_that_is_not_json_is_kept_as_where_we_are() -> None:
    summary = parse_summary("Things are fine.", owners=set())
    assert summary.where_we_are == "Things are fine." and summary.next_steps == []


def test_prompts_carry_the_facts_and_the_reports() -> None:
    m = Member(employee_id="sales-head", name="Sam", title="Head of Sales", role="Sells", model="m")
    facts = Facts(done=[task("Call Acme", "completed", 2, "Demo booked")])
    system, user = update_prompt(m, facts)
    assert "Head of Sales" in system and "Call Acme" in user and "Demo booked" in user
    ceo = Member(employee_id="co-ceo", name="Cora", title="Co-CEO", model="m")
    reports = [Report(employee_id="sales-head", title="Head of Sales", done="Booked a demo")]
    system, user = summary_prompt(ceo, reports, [("maria", "Focus on Acme")], scope="company")
    assert "Co-CEO" in system and "Booked a demo" in user and "Focus on Acme" in user


def test_the_co_ceo_leads_and_the_scope_picks_who_reports() -> None:
    from core_python.collaboration.domain.standup import pick_roster

    people = [
        Member(employee_id="mk", name="M", department_id="marketing", model=""),
        Member(employee_id="ceo", name="C", is_co_ceo=True, model=""),
        Member(employee_id="sh", name="S", department_id="sales", model=""),
        Member(employee_id="si", name="I", department_id="sales", model=""),
    ]
    ceo, members = pick_roster(people, "")
    assert ceo is not None and ceo.employee_id == "ceo"
    assert [m.employee_id for m in members] == ["ceo", "mk", "sh", "si"]
    ceo, members = pick_roster(people, "sales")
    assert ceo is not None and [m.employee_id for m in members] == ["sh", "si"]
    assert pick_roster([p for p in people if not p.is_co_ceo], "")[0] is None
