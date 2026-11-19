"""The identity core (brain.md section 2): who the employee is, pinned in every prompt.

Only humans change it, through an approved proposal (Phase 8), and every change is a new
brain version. Here: the shape of it and how it renders into the pinned prompt block.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Identity(BaseModel):
    role: str = Field(min_length=1)  # the role charter, in a sentence or two
    persona: str = ""
    values: list[str] = Field(default_factory=list)
    style: str = ""  # how it communicates
    working_hours: str = ""
    hard_constraints: list[str] = Field(default_factory=list)  # never do these
    escalation: list[str] = Field(default_factory=list)  # when to ask a human

    def render(self) -> str:
        """The pinned prompt block. Stable text, so prompt caching works across steps."""
        parts = [f"Role: {self.role}"]
        if self.persona:
            parts.append(f"Persona: {self.persona}")
        if self.values:
            parts.append("Values:\n" + "\n".join(f"- {v}" for v in self.values))
        if self.style:
            parts.append(f"Communication style: {self.style}")
        if self.working_hours:
            parts.append(f"Working hours: {self.working_hours}")
        if self.hard_constraints:
            parts.append("Never:\n" + "\n".join(f"- {c}" for c in self.hard_constraints))
        if self.escalation:
            parts.append("Ask a human when:\n" + "\n".join(f"- {e}" for e in self.escalation))
        return "\n\n".join(parts)
