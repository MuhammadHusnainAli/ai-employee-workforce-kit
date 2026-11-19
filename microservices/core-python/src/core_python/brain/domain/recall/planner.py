"""The query planner (brain.md section 3, step 2): which stores a step needs, and how much."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core_python.brain.domain.memory.model import Kind

StepType = Literal["planning", "acting", "replying", "live_meeting"]

DEFAULT_BUDGET = 2_000


@dataclass(frozen=True)
class Plan:
    kinds: tuple[Kind, ...]
    limit: int  # candidates per retriever
    budget: int  # tokens for identity, rules and memories together


def plan(step_type: StepType, token_budget: int = 0) -> Plan:
    """Planning looks at past outcomes and lessons; acting at facts and similar episodes;
    replying and live meetings at facts only, and live meetings keep it small for latency."""
    budget = token_budget or DEFAULT_BUDGET
    match step_type:
        case "planning":
            return Plan(("episodic", "semantic", "reflection"), limit=40, budget=budget)
        case "replying":
            return Plan(("semantic",), limit=30, budget=budget)
        case "live_meeting":
            return Plan(("semantic",), limit=15, budget=min(budget, 600))
        case _:
            return Plan(("semantic", "episodic"), limit=30, budget=budget)


def estimate_tokens(text: str) -> int:
    """Same rough rule as the harness: about four characters a token, never zero."""
    return max(1, len(text) // 4)
