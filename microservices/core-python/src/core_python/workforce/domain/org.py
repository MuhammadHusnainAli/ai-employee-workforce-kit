"""The org chart: who reports to whom, the co-CEO, who may hand work to whom. No I/O.

The person who owns the workspace is the CEO and is not an employee. Exactly one employee may be
the co-CEO; it reports to the person (no manager). Anyone else without a manager also reports to
the person directly.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

DEPARTMENT_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class InvalidPlacement(ValueError):
    pass


class Member(BaseModel):
    employee_id: str
    name: str
    title: str = ""
    department_id: str | None = None
    manager_id: str | None = None
    is_co_ceo: bool = False
    status: str = "active"


def below(members: dict[str, Member], manager_id: str) -> set[str]:
    """Everyone who reports to `manager_id`, directly or through others."""
    reports: dict[str, list[str]] = {}
    for m in members.values():
        if m.manager_id:
            reports.setdefault(m.manager_id, []).append(m.employee_id)
    found: set[str] = set()
    todo = list(reports.get(manager_id, []))
    while todo:
        eid = todo.pop()
        if eid in found or eid == manager_id:
            continue
        found.add(eid)
        todo.extend(reports.get(eid, []))
    return found


def check_placement(
    members: dict[str, Member], who: str, manager_id: str | None, *, is_co_ceo: bool
) -> None:
    """Raise InvalidPlacement unless `who` may report to `manager_id` (None: the person)."""
    if is_co_ceo:
        if manager_id:
            raise InvalidPlacement("the co-CEO reports to the person, so it has no manager")
        other = next((m for m in members.values() if m.is_co_ceo and m.employee_id != who), None)
        if other is not None:
            raise InvalidPlacement(f"{other.employee_id!r} is already the co-CEO")
    if not manager_id:
        return
    if manager_id == who or manager_id in below(members, who):
        raise InvalidPlacement("that reporting line would loop back on itself")
    manager = members.get(manager_id)
    if manager is None:
        raise InvalidPlacement(f"no employee {manager_id!r} to report to")
    if manager.status != "active":
        raise InvalidPlacement(f"{manager_id!r} is {manager.status}")


def delegation_refusal(members: dict[str, Member], from_id: str, to_id: str) -> str | None:
    """Why `from_id` may not hand work to `to_id`, or None when it may. The co-CEO may hand
    work to any active employee but itself; anyone else to the active employees below them."""
    sender, target = members.get(from_id), members.get(to_id)
    if sender is None:
        return f"there is no employee {from_id!r}"
    if target is None:
        return f"there is no employee {to_id!r}"
    if from_id == to_id:
        return "you cannot hand work to yourself"
    if target.status != "active":
        return f"{to_id} is {target.status}"
    if not sender.is_co_ceo and to_id not in below(members, from_id):
        return f"{to_id} is not below you in the org chart; ask them or their manager instead"
    return None


def can_delegate(members: dict[str, Member], from_id: str, to_id: str) -> bool:
    return delegation_refusal(members, from_id, to_id) is None


def valid_department_id(department_id: str) -> bool:
    return bool(DEPARTMENT_ID.match(department_id))
