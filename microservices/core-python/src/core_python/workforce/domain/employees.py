"""Employees and their versions: the rules a version must follow. No I/O."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Tier = Literal["R0", "R1", "R2", "R3"]
EMPLOYEE_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class InvalidVersion(ValueError):
    pass


class ToolGrant(BaseModel):
    tool: str
    tier: Tier

    @field_validator("tool")
    @classmethod
    def _name(cls, v: str) -> str:
        if not TOOL_NAME.match(v):
            raise ValueError(f"bad tool name {v!r}")
        return v


class Limits(BaseModel):
    max_steps: int = Field(default=20, ge=1, le=200)
    max_cost_usd: Decimal = Field(default=Decimal("1.00"), gt=0, le=100)


class VersionSpec(BaseModel):
    """What one published version says. Immutable once stored."""

    role: str = Field(min_length=1, max_length=2_000)
    instructions: str = Field(max_length=50_000)
    model: str = Field(min_length=1, max_length=200)
    tool_grants: list[ToolGrant] = Field(default_factory=list)
    auto_approve: list[str] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def _rules(self) -> VersionSpec:
        names = [g.tool for g in self.tool_grants]
        if len(names) != len(set(names)):
            raise ValueError("a tool is granted twice")
        tiers = {g.tool: g.tier for g in self.tool_grants}
        for tool in self.auto_approve:
            if tool not in tiers:
                raise ValueError(f"auto_approve names {tool!r}, which is not granted")
            if tiers[tool] != "R2":
                # R0/R1 never ask anyway; R3 always asks (agent-harness.md, stage 4).
                raise ValueError(f"only R2 tools can be auto-approved; {tool!r} is {tiers[tool]}")
        return self


def valid_employee_id(employee_id: str) -> bool:
    return bool(EMPLOYEE_ID.match(employee_id))
