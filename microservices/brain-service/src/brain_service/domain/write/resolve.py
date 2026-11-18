"""The resolver (brain.md section 4, step 4). Pure: no I/O.

For each governed candidate, given the employee's current memories that could clash with it:

- **reinforce** a near-duplicate instead of storing the same thing twice;
- **supersede** an older value of the same fact (same `fact_key`): its validity is closed, its
  history kept. Only when the newer one is at least as trusted and reasonably confident;
- **flag** the contradiction for a human otherwise: the newer one is stored quarantined,
  pointing at the fact it contradicts;
- **insert** everything else.

Episodes are never merged: each one is its own experience.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from brain_service.domain.memory.model import Kind, Trust
from brain_service.domain.write.model import Governed

#: Cosine distance under which two memories of the same kind say the same thing.
DUPLICATE_DISTANCE = 0.05
#: A newer fact needs at least this confidence to replace an older one on its own.
SUPERSEDE_CONFIDENCE = 0.6
TRUST_RANK: dict[Trust, int] = {"external": 0, "observed": 1, "system": 2, "human_verified": 3}


@dataclass(frozen=True)
class Existing:
    """A current (valid, active) memory near the candidate."""

    id: uuid.UUID
    kind: Kind
    content: str
    trust: Trust
    confidence: float
    fact_key: str | None
    distance: float


@dataclass(frozen=True)
class Decision:
    action: Literal["insert", "reinforce", "supersede", "flag"]
    #: The existing memory reinforced, superseded or contradicted.
    target: uuid.UUID | None = None


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def resolve(g: Governed, nearby: list[Existing]) -> Decision:
    c = g.candidate
    if c.kind == "episodic":
        return Decision("insert")
    same_kind = [e for e in nearby if e.kind == c.kind]
    for e in sorted(same_kind, key=lambda e: e.distance):
        if e.distance <= DUPLICATE_DISTANCE or _same_text(e.content, g.content):
            return Decision("reinforce", e.id)
    if g.status == "quarantined":
        return Decision("insert")  # waits for a person; it can never replace anything
    if c.key:
        for e in same_kind:
            if e.fact_key == c.key:
                stronger = TRUST_RANK[g.trust] >= TRUST_RANK[e.trust]
                if stronger and c.confidence >= SUPERSEDE_CONFIDENCE:
                    return Decision("supersede", e.id)
                return Decision("flag", e.id)
    return Decision("insert")
