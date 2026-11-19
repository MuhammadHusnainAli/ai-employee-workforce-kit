"""The memory model (brain.md section 2). Plain data and invariants; no I/O."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Kind = Literal["episodic", "semantic", "reflection"]
Scope = Literal["employee", "team", "tenant"]
Trust = Literal["human_verified", "system", "observed", "external"]


def now() -> datetime:
    return datetime.now(UTC)


class Memory(BaseModel):
    """One long-term memory. Bi-temporal: valid_from/valid_to (true in the world) and
    recorded_at (known to the brain). Nothing is overwritten; a newer fact closes validity."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID
    employee_id: str | None
    scope: Scope = "employee"
    kind: Kind
    content: str = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    trust: Trust
    source_ref: str | None = None
    valid_from: datetime = Field(default_factory=now)
    valid_to: datetime | None = None
    recorded_at: datetime = Field(default_factory=now)

    @model_validator(mode="after")
    def _invariants(self) -> Memory:
        if self.scope != "tenant" and not self.employee_id:
            raise ValueError("employee and team memories belong to an employee")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self

    def age_days(self, at: datetime) -> float:
        """How old the memory is at `at`, for time decay."""
        return max((at - self.recorded_at).total_seconds() / 86_400, 0.0)
