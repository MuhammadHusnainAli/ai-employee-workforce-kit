"""Hiring: what a hire says and the tools a new hire starts with. No I/O.

A hire costs money, so only a person hires. An employee can only request a hire, which waits for
a person's decision.

Everyone gets the delegation tools: the chart decides who they may hand work to, so an employee
with nobody below them is simply refused. That keeps a version valid when someone is later given
reports, without publishing a new one.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from core_python.workforce.domain.employees import Limits, Tier, ToolGrant, VersionSpec

EVERYONE: dict[str, Tier] = {
    "list_team": "R0",
    "read_messages": "R0",
    "send_message": "R1",
    "ask_colleague": "R1",
    "delegate_task": "R1",
    "wait_for_reports": "R0",
}
CO_CEO_ONLY: dict[str, Tier] = {"request_hire": "R1", "start_standup": "R1"}


def default_grants(*, is_co_ceo: bool) -> list[ToolGrant]:
    tools = EVERYONE | (CO_CEO_ONLY if is_co_ceo else {})
    return [ToolGrant(tool=name, tier=tier) for name, tier in tools.items()]


class Hire(BaseModel):
    """Who to hire, where they sit, and what their first version says."""

    employee_id: str
    name: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=200)
    department_id: str | None = None
    manager_id: str | None = None
    is_co_ceo: bool = False
    department_head: bool = False
    role: str = Field(min_length=1, max_length=2_000)
    instructions: str = Field(default="", max_length=50_000)
    model: str = Field(min_length=1, max_length=200)
    max_cost_usd: Decimal = Field(default=Decimal("1.00"), gt=0, le=100)

    def version(self) -> VersionSpec:
        return VersionSpec(
            role=self.role,
            instructions=self.instructions,
            model=self.model,
            tool_grants=default_grants(is_co_ceo=self.is_co_ceo),
            limits=Limits(max_cost_usd=self.max_cost_usd),
        )
