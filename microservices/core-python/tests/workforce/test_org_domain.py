"""Org chart rules: reporting lines, the co-CEO, who may delegate to whom. No I/O."""

import pytest

from core_python.workforce.domain.org import (
    InvalidPlacement,
    Member,
    below,
    can_delegate,
    check_placement,
)


def m(eid: str, manager: str | None = None, *, ceo: bool = False, status: str = "active") -> Member:
    return Member(employee_id=eid, name=eid, manager_id=manager, is_co_ceo=ceo, status=status)


def chart() -> dict[str, Member]:
    people = [
        m("ceo", ceo=True),
        m("head", "ceo"),
        m("junior", "head"),
        m("intern", "junior"),
        m("marketing", "ceo"),
        m("loner"),
    ]
    return {p.employee_id: p for p in people}


def test_below_is_everyone_under_a_manager() -> None:
    assert below(chart(), "head") == {"junior", "intern"}
    assert below(chart(), "ceo") == {"head", "junior", "intern", "marketing"}
    assert below(chart(), "intern") == set()


def test_reporting_line_cannot_loop() -> None:
    with pytest.raises(InvalidPlacement, match="loop"):
        check_placement(chart(), "head", "intern", is_co_ceo=False)
    with pytest.raises(InvalidPlacement, match="loop"):
        check_placement(chart(), "head", "head", is_co_ceo=False)


def test_manager_must_exist_and_be_active() -> None:
    with pytest.raises(InvalidPlacement, match="no employee"):
        check_placement(chart(), "junior", "ghost", is_co_ceo=False)
    people = chart()
    people["marketing"] = m("marketing", "ceo", status="paused")
    with pytest.raises(InvalidPlacement, match="paused"):
        check_placement(people, "junior", "marketing", is_co_ceo=False)


def test_only_one_co_ceo_and_it_reports_to_the_person() -> None:
    with pytest.raises(InvalidPlacement, match="already"):
        check_placement(chart(), "loner", None, is_co_ceo=True)
    with pytest.raises(InvalidPlacement, match="person"):
        check_placement(chart(), "ceo", "head", is_co_ceo=True)
    check_placement(chart(), "ceo", None, is_co_ceo=True)  # placing the same co-CEO again


def test_a_valid_move_passes() -> None:
    check_placement(chart(), "intern", "head", is_co_ceo=False)
    check_placement(chart(), "loner", None, is_co_ceo=False)


def test_co_ceo_delegates_to_anyone_active_but_itself() -> None:
    people = chart()
    assert can_delegate(people, "ceo", "loner")
    assert can_delegate(people, "ceo", "intern")
    assert not can_delegate(people, "ceo", "ceo")
    people["loner"] = m("loner", status="paused")
    assert not can_delegate(people, "ceo", "loner")


def test_others_delegate_only_below_themselves() -> None:
    people = chart()
    assert can_delegate(people, "head", "intern")
    assert not can_delegate(people, "head", "marketing")
    assert not can_delegate(people, "junior", "head")
    assert not can_delegate(people, "ghost", "intern")
    assert not can_delegate(people, "head", "ghost")
