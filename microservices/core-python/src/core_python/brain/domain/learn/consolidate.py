"""Consolidation, the nightly "sleep" (brain.md section 5). Pure: no I/O.

- **Cluster** similar episodes (cosine similarity of their embeddings).
- **Summarise** each cluster into one fact that cites the episodes it came from.
- **Reflect**: a kind of task that keeps failing becomes a lesson. Advisory: low confidence.
- **Decay**: what has not been used for a while loses importance; below the floor it is
  archived (kept, never recalled). What a person confirmed does not decay.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core_python.brain.domain.write.model import Candidate, Outcome

#: Episodes at least this similar are the same kind of task.
SIMILAR = 0.8
#: A pattern needs at least this many episodes.
MIN_CLUSTER = 2
#: A lesson needs this many tries, failing at least this often.
REFLECT_MIN = 3
REFLECT_FAILURE_RATE = 0.5
#: Per pass, for memories unused for UNUSED_DAYS; archived below FLOOR.
DECAY = 0.95
FLOOR = 0.05
UNUSED_DAYS = 30


@dataclass(frozen=True)
class Episode:
    memory_id: str
    task: str
    outcome: Outcome
    embedding: list[float]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def clusters(episodes: list[Episode], similar: float = SIMILAR) -> list[list[Episode]]:
    """Greedy, in order: each episode joins the first cluster whose first episode it resembles.
    Deterministic for the same input. Only clusters of MIN_CLUSTER or more are returned."""
    groups: list[list[Episode]] = []
    for ep in episodes:
        for g in groups:
            if cosine(ep.embedding, g[0].embedding) >= similar:
                g.append(ep)
                break
        else:
            groups.append([ep])
    return [g for g in groups if len(g) >= MIN_CLUSTER]


def _example(group: list[Episode]) -> str:
    task = min((e.task for e in group if e.task.strip()), key=len, default="this kind of task")
    task = " ".join(task.split())
    return task if len(task) <= 120 else task[:117] + "..."


def _counts(group: list[Episode]) -> dict[str, int]:
    out = {"success": 0, "failure": 0, "escalated": 0, "unknown": 0}
    for e in group:
        out[e.outcome] = out.get(e.outcome, 0) + 1
    return out


def summarise(group: list[Episode]) -> Candidate:
    """One fact for a cluster: how often this kind of task came up and how it went."""
    n = len(group)
    c = _counts(group)
    parts = [f"{c['success']} succeeded"]
    if c["failure"]:
        parts.append(f"{c['failure']} failed")
    if c["escalated"]:
        parts.append(f"{c['escalated']} needed a person")
    return Candidate(
        kind="semantic",
        content=f'I have done {n} tasks like "{_example(group)}": {", ".join(parts)}.',
        importance=min(0.4 + 0.05 * n, 0.8),
        confidence=0.8,
        origin="consolidation",
        cites=[e.memory_id for e in group],
    )


def reflect(group: list[Episode]) -> Candidate | None:
    """A lesson when this kind of task keeps failing. Advisory, so low confidence."""
    n = len(group)
    failed = _counts(group)["failure"]
    if n < REFLECT_MIN or failed / n < REFLECT_FAILURE_RATE:
        return None
    return Candidate(
        kind="reflection",
        content=(
            f'Tasks like "{_example(group)}" often fail for me ({failed} of {n}). '
            "Check the approach before starting, and ask a person early."
        ),
        importance=0.7,
        confidence=0.5,
        origin="consolidation",
        cites=[e.memory_id for e in group],
    )


def distil(episodes: list[Episode]) -> list[Candidate]:
    """Everything one pass learns from a batch of episodes."""
    out: list[Candidate] = []
    for g in clusters(episodes):
        out.append(summarise(g))
        lesson = reflect(g)
        if lesson is not None:
            out.append(lesson)
    return out


def decayed(importance: float) -> tuple[float, bool]:
    """The importance after one pass of disuse, and whether the memory is archived."""
    new = round(importance * DECAY, 6)
    return new, new < FLOOR
