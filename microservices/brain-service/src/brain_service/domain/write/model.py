"""The write path's data (brain.md section 4): where a memory comes from, what the extractor
proposes, and what governance lets through. Plain data and invariants; no I/O."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from brain_service.domain.graph.model import EntityRef, RelationRef
from brain_service.domain.memory.model import Kind, Trust

Outcome = Literal["success", "failure", "escalated", "unknown"]
Status = Literal["active", "quarantined"]
PiiPolicy = Literal["keep", "redact", "drop"]


class Source(BaseModel):
    """The event a memory is learned from. Its id makes every write idempotent."""

    event_id: uuid.UUID
    event_type: str
    tenant_id: uuid.UUID
    employee_id: str
    run_id: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    #: What happened, for the extractor: the task, the output, how it ended.
    task: str = ""
    output: str = ""
    outcome: Outcome = "unknown"
    stop_reason: str = ""
    #: The tools the run called, in order (skill mining).
    tools: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    """One memory the extractor proposes. `origin` says where in the source it came from and
    decides its trust tag (brain_core.trust_for): the run itself, its recorded outcome, a tool
    result, an inbound email..."""

    kind: Kind
    content: str = Field(min_length=1, max_length=4000)
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.8, ge=0, le=1)
    origin: str = "run"
    #: For semantic facts: a stable subject and attribute ("ana.meeting_day"), so a newer
    #: value of the same fact can supersede the older one.
    key: str | None = Field(default=None, max_length=200)
    #: Who and what it is about (the semantic graph), and the relations it states.
    mentions: list[EntityRef] = Field(default_factory=list, max_length=20)
    relations: list[RelationRef] = Field(default_factory=list, max_length=10)
    #: The memories it was distilled from (consolidation), so it can always be explained.
    cites: list[str] = Field(default_factory=list, max_length=200)


class Governed(BaseModel):
    """A candidate after the governance filter: safe to store."""

    candidate: Candidate
    content: str
    trust: Trust
    status: Status
    #: What the filter found: {"secret": 1, "email": 2}, and "instructions" when the text tries
    #: to instruct the employee (stored as data only, never a rule).
    findings: dict[str, int] = Field(default_factory=dict)
    #: Mentions and relations that passed governance too (names with secrets or, under the
    #: tenant's policy, personal data are left out). Empty for quarantined memories.
    mentions: list[EntityRef] = Field(default_factory=list)
    relations: list[RelationRef] = Field(default_factory=list)


class Committed(BaseModel):
    """What one source added to the brain."""

    source_event_id: uuid.UUID
    memory_ids: list[uuid.UUID] = Field(default_factory=list)
    #: inserted, reinforced, superseded, flagged, quarantined, dropped
    counts: dict[str, int] = Field(default_factory=dict)
    #: True when this event had been committed before: nothing was written now.
    already: bool = False
