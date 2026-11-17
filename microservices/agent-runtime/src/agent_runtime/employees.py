"""Where an employee's definition comes from at run time.

Phase 4 reads pinned versions from the Workforce Service. Until then a directory in code
serves them, behind the same interface.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from agent_runtime.harness.state import EmployeeSpec


class EmployeeNotFound(Exception):
    pass


class EmployeeDirectory(Protocol):
    def get(self, employee_id: str) -> EmployeeSpec: ...


class StaticDirectory:
    """Employees defined in code. Each get() builds a fresh spec, like a fresh worker would."""

    def __init__(self, factories: dict[str, Callable[[], EmployeeSpec]]) -> None:
        self.factories = factories

    def get(self, employee_id: str) -> EmployeeSpec:
        try:
            return self.factories[employee_id]()
        except KeyError:
            raise EmployeeNotFound(f"no employee {employee_id!r}") from None
