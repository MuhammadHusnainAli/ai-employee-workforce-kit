"""Skill mining (brain.md section 5). Pure: no I/O.

When an employee has succeeded at the same kind of task MIN_EPISODES times or more, with the
same tools in the same order every time, that way of working becomes a skill proposal. A
person approves it like a rule; once active, it is recalled when a new task looks like its
trigger, and the employee is told which tools worked before and in what order.
"""

from __future__ import annotations

from dataclasses import dataclass

from core_python.brain.domain.learn.consolidate import SIMILAR, cosine

#: How many similar successes with one tool sequence make a skill.
MIN_EPISODES = 5
#: How close a task must be to a skill's trigger for the skill to be recalled.
MATCH = 0.75
#: At most this many skills per recall.
MAX_RECALLED = 2


@dataclass(frozen=True)
class Success:
    """One successful episode and the tools its run used, in order."""

    memory_id: str
    task: str
    tools: tuple[str, ...]
    embedding: list[float]


@dataclass(frozen=True)
class MinedSkill:
    name: str
    trigger: str
    tools: tuple[str, ...]
    procedure: str
    evidence: tuple[str, ...]


def _example(group: list[Success]) -> str:
    task = min((s.task for s in group if s.task.strip()), key=len, default="this kind of task")
    task = " ".join(task.split())
    return task if len(task) <= 200 else task[:197] + "..."


def describe(trigger: str, tools: tuple[str, ...]) -> tuple[str, str]:
    """A skill's name and the procedure the employee is shown."""
    name = " then ".join(dict.fromkeys(tools))
    steps = "\n".join(f"{i}. Use {t}." for i, t in enumerate(tools, 1))
    procedure = (
        f'For tasks like "{trigger}", this worked before:\n{steps}\n'
        f"{len(tools) + 1}. Answer from what the tools returned."
    )
    return name if len(name) <= 120 else name[:117] + "...", procedure


def mine(
    successes: list[Success], known: set[tuple[str, ...]], similar: float = SIMILAR
) -> list[MinedSkill]:
    """Skills in a set of successes. Deterministic for the same input: groups by tool sequence
    first, then greedily by similarity to each cluster's first task. Tool sequences already
    known (proposed, active, rejected or retired) are skipped."""
    by_tools: dict[tuple[str, ...], list[Success]] = {}
    for s in successes:
        if s.tools and s.tools not in known:
            by_tools.setdefault(s.tools, []).append(s)
    out: list[MinedSkill] = []
    for tools, group in by_tools.items():
        if len(group) < MIN_EPISODES:
            continue
        clusters: list[list[Success]] = []
        for s in group:
            for c in clusters:
                if cosine(s.embedding, c[0].embedding) >= similar:
                    c.append(s)
                    break
            else:
                clusters.append([s])
        best = max(clusters, key=len)
        if len(best) < MIN_EPISODES:
            continue
        trigger = _example(best)
        name, procedure = describe(trigger, tools)
        out.append(MinedSkill(name, trigger, tools, procedure,
                              tuple(s.memory_id for s in best)))  # fmt: skip
    return out
