"""Hiring rules: the tools a new hire starts with. No I/O."""

from core_python.workforce.domain.hiring import CO_CEO_ONLY, EVERYONE, default_grants


def test_everyone_can_talk_and_hand_work_down() -> None:
    names = {g.tool: g.tier for g in default_grants(is_co_ceo=False)}
    assert names == {
        "list_team": "R0",
        "read_messages": "R0",
        "send_message": "R1",
        "ask_colleague": "R1",
        "delegate_task": "R1",
        "wait_for_reports": "R0",
    }
    assert set(names) == set(EVERYONE)


def test_only_the_co_ceo_requests_hires_and_runs_standups() -> None:
    co_ceo = {g.tool for g in default_grants(is_co_ceo=True)}
    assert co_ceo == set(EVERYONE) | {"request_hire", "start_standup"}
    assert set(CO_CEO_ONLY) == {"request_hire", "start_standup"}
