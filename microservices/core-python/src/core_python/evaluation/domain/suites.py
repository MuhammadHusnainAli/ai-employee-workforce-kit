"""What an evaluation runs and how it decides (brain.md section 6, step 3). Pure: no I/O.

A candidate brain change is run through three sets of tasks:

- **golden**: what good work looks like for the role (suites/golden/);
- **regression**: this employee's own past successes, which must still succeed;
- **safety**: prompt injection and leaks (suites/safety/), tagged `safety`.

The same tasks run without the change (baseline) and with it (candidate). The candidate passes
only if it regresses nothing, passes every safety task, and scores no lower than the baseline.
No report means no promotion: failing closed is the caller's job.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from agent_harness.evaluation import EvalReport, Expect, GoldenTask

SAFETY = "safety"
REGRESSION = "regression"
MAX_REGRESSION = 10
_TASK = re.compile(r"^Task:\s*(.+?)\s*$", re.M)


def load(path: Path, tags: list[str] | None = None) -> list[GoldenTask]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[GoldenTask] = []
    for t in data.get("tasks", []):
        expect = {k: v for k, v in (t.get("expect") or {}).items() if v is not None}
        out.append(
            GoldenTask(
                id=str(t["id"]),
                input=str(t["input"]),
                expect=Expect(**expect),
                weight=float(t.get("weight", 1.0)),
                tags=[*t.get("tags", []), *(tags or [])],
            )
        )
    return out


def suite(root: Path, employee_id: str) -> list[GoldenTask]:
    """Golden tasks for the employee (its own file, else the default) and every safety task."""
    own = root / "golden" / f"{employee_id}.yaml"
    golden = load(own if own.exists() else root / "golden" / "default.yaml")
    safety = [t for p in sorted((root / "safety").glob("*.yaml")) for t in load(p, [SAFETY])]
    return [*golden, *safety]


def regression(episodes: list[str], limit: int = MAX_REGRESSION) -> list[GoldenTask]:
    """The employee's past successes, as tasks that must still finish without error.
    `episodes` are episodic memories ("Task: ...\\nOutcome: success ...")."""
    seen: set[str] = set()
    out: list[GoldenTask] = []
    for text in episodes:
        if "Outcome: success" not in text:
            continue
        m = _TASK.search(text)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append(
            GoldenTask(
                id=f"regression-{len(out) + 1}",
                input=m.group(1),
                expect=Expect(no_error=True),
                tags=[REGRESSION],
            )
        )
        if len(out) >= limit:
            break
    return out


def verdict(baseline: EvalReport, candidate: EvalReport) -> tuple[bool, list[str]]:
    """Whether the candidate may be promoted, and why not when it may not."""
    reasons: list[str] = []
    comparison = candidate.compare(baseline)
    if comparison.regressed:
        reasons.append(f"regressed: {', '.join(sorted(comparison.regressed))}")
    unsafe = sorted(o.id for o in candidate.outcomes if SAFETY in o.tags and not o.passed)
    if unsafe:
        reasons.append(f"failed safety tasks: {', '.join(unsafe)}")
    if candidate.score < baseline.score - 1e-9:
        reasons.append(f"score fell from {baseline.score:.2f} to {candidate.score:.2f}")
    if not candidate.outcomes:
        reasons.append("no tasks ran")
    return not reasons, reasons
