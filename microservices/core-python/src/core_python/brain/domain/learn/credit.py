"""Credit assignment (brain.md section 6). Pure: no I/O.

`recall_log` records which memories each step of a run used. When the run's outcome is known,
credit or blame flows back to exactly those memories: helpful ones gain importance, misleading
ones lose it. A person's rating (Feedback) moves them more than an outcome does.
"""

from __future__ import annotations

from core_python.brain.domain.write.model import Outcome

#: Importance change per outcome for each memory the run recalled.
OUTCOME_DELTA: dict[Outcome, float] = {
    "success": 0.03,
    "failure": -0.05,  # misleading memories should fall faster than useful ones rise
    "escalated": 0.0,  # the run stopped for a person: says nothing about what it recalled
    "unknown": 0.0,
}


def delta(outcome: Outcome) -> float:
    return OUTCOME_DELTA.get(outcome, 0.0)
