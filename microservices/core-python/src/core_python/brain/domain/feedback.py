"""Feedback and what it becomes (brain.md section 6, learning signals). Pure: no I/O.

| kind              | from        | becomes                                                    |
|-------------------|-------------|------------------------------------------------------------|
| correction        | a person    | a rule proposal; the rule is active only once approved     |
| correction        | anyone else | recorded, nothing more: only people can create rules       |
| rating            | anyone      | credit or blame for the memories the run recalled          |
| approval_rejected | a person    | a lesson (reflection memory) through the governed write path |
| comment           | anyone      | recorded                                                   |
"""

from __future__ import annotations

import uuid
from typing import Literal

import brain_core
from pydantic import BaseModel, Field

FeedbackKind = Literal["correction", "rating", "approval_rejected", "comment"]
ActorKind = Literal["human", "employee", "service"]
Risk = Literal["low", "medium", "high"]

#: How much one rating moves the importance of each memory the run recalled.
RATING_STEP = 0.05
MAX_TEXT = 2000
SECRET_MARK = "[secret removed]"  # noqa: S105 - brain-core's placeholder, not a secret


class FeedbackIn(BaseModel):
    tenant_id: uuid.UUID
    employee_id: str = Field(min_length=1)
    run_id: str | None = None
    kind: FeedbackKind
    text: str = ""
    rating: Literal[-1, 1] | None = None
    actor_id: str = Field(min_length=1)
    actor_kind: ActorKind
    idempotency_key: str = Field(min_length=1, max_length=200)

    @property
    def id(self) -> uuid.UUID:
        """Stable per (tenant, key): a retried call names the same feedback."""
        return uuid.uuid5(self.tenant_id, f"feedback/{self.idempotency_key}")


def problem(f: FeedbackIn) -> str | None:
    """Why the feedback cannot be accepted, or None."""
    text = f.text.strip()
    if len(text) > MAX_TEXT:
        return f"text is at most {MAX_TEXT} characters"
    match f.kind:
        case "correction" | "approval_rejected" | "comment":
            if not text:
                return f"{f.kind} needs text"
        case "rating":
            if f.rating is None:
                return "rating needs rating = 1 or -1"
            if not f.run_id:
                return "rating needs the run it rates"
    return None


def proposes_rule(f: FeedbackIn) -> bool:
    """Only verified human feedback can create rules (brain.md section 1)."""
    return f.kind == "correction" and f.actor_kind == "human"


def rule_text(text: str) -> str | None:
    """The rule as it will be pinned: secrets removed, personal data kept (a person wrote it on
    purpose). None when nothing is left."""
    governed, _, _ = brain_core.govern(text.strip(), "keep")
    if not governed or not governed.replace(SECRET_MARK, "").strip(" .,;:-"):
        return None
    return governed.strip()


def risk(text: str) -> Risk:
    """How carefully a rule must be reviewed. A rule about money, sending outside or deleting
    is high risk; everything else medium. (Low is for facts, which skip approval.)"""
    t = text.lower()
    high = ("pay", "refund", "transfer", "wire", "invoice", "delete", "external", "forward",
            "share", "send to", "password", "access")  # fmt: skip
    return "high" if any(w in t for w in high) else "medium"
