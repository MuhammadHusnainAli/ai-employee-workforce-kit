"""Standups: what each employee did, from real task status, and what the co-CEO makes of it.
No I/O.

Kept cheap on purpose: an employee with nothing new since the last standup gets a fixed line and
no model call; the others get one short model call each; the co-CEO writes one summary.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from core_python.collaboration.domain.conversations import PERSON

#: Next steps one standup may hand out.
MAX_NEXT_STEPS = 10
#: Updates written at the same time.
UPDATE_BATCH = 5
#: How long the person has to speak up before the discussion closes, by default.
DISCUSSION_S = 120
IDLE_LINE = "Nothing new since the last standup."
FACT_OUTPUT_CHARS = 300


class Member(BaseModel):
    employee_id: str
    name: str
    title: str = ""
    role: str = ""
    department_id: str = ""
    manager_id: str = ""
    is_co_ceo: bool = False
    model: str


class TaskFact(BaseModel):
    title: str
    status: str
    updated_at: datetime
    output: str = ""


class Facts(BaseModel):
    done: list[TaskFact] = Field(default_factory=list)
    in_progress: list[TaskFact] = Field(default_factory=list)
    waiting: list[TaskFact] = Field(default_factory=list)
    failed: list[TaskFact] = Field(default_factory=list)

    @property
    def idle(self) -> bool:
        return not (self.done or self.in_progress or self.waiting or self.failed)


class Report(BaseModel):
    employee_id: str
    title: str = ""
    done: str = ""
    next: str = ""
    blockers: str = ""
    idle: bool = False

    def text(self) -> str:
        if self.idle:
            return self.done
        parts = [f"Done: {self.done or '-'}", f"Next: {self.next or '-'}"]
        if self.blockers:
            parts.append(f"Blocked: {self.blockers}")
        return "\n".join(parts)


class NextStep(BaseModel):
    owner: str
    action: str
    task_id: str = ""


class Summary(BaseModel):
    where_we_are: str = ""
    where_we_go: str = ""
    next_steps: list[NextStep] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    def text(self) -> str:
        lines = [f"Where we are: {self.where_we_are}", f"Where we go: {self.where_we_go}"]
        if self.next_steps:
            lines.append("Next steps:")
            lines += [f"- {s.owner}: {s.action}" for s in self.next_steps]
        if self.risks:
            lines.append("Risks: " + "; ".join(self.risks))
        return "\n".join(lines)


def facts_from(tasks: list[TaskFact], *, since: datetime) -> Facts:
    """Finished work counts when it finished since the last standup; open work always counts."""
    facts = Facts()
    for t in tasks:
        recent = t.updated_at >= since
        match t.status:
            case "completed" if recent:
                facts.done.append(t)
            case "failed" | "cancelled" if recent:
                facts.failed.append(t)
            case "running" | "queued":
                facts.in_progress.append(t)
            case "waiting_human":
                facts.waiting.append(t)
            case _:
                pass
    return facts


def idle_report(m: Member) -> Report:
    return Report(employee_id=m.employee_id, title=m.title, done=IDLE_LINE, idle=True)


def _who(m: Member) -> str:
    return f"{m.name}, {m.title}" if m.title else m.name


def _facts_text(facts: Facts) -> str:
    def block(name: str, items: list[TaskFact]) -> list[str]:
        out = [f"{name}:"] if items else []
        for t in items:
            line = f"- {t.title}"
            if t.output.strip():
                line += f": {t.output.strip()[:FACT_OUTPUT_CHARS]}"
            out.append(line)
        return out

    lines = (
        block("Finished", facts.done)
        + block("In progress", facts.in_progress)
        + block("Waiting for the person", facts.waiting)
        + block("Failed", facts.failed)
    )
    return "\n".join(lines)


JSON_ONLY = "Reply with JSON only."


def update_prompt(m: Member, facts: Facts) -> tuple[str, str]:
    system = (
        f"You are {_who(m)}. {m.role.strip()}\n\nIt is the standup. Say in a sentence each what "
        "you finished, what you do next and what blocks you, using only the facts given. "
        f'{JSON_ONLY} {{"done": "...", "next": "...", "blockers": ""}}'
    )
    return system, "Your tasks since the last standup:\n" + _facts_text(facts)


def summary_prompt(
    ceo: Member, reports: list[Report], discussion: list[tuple[str, str]], *, scope: str
) -> tuple[str, str]:
    system = (
        f"You are {_who(ceo)}, running the {scope} standup for the CEO (the person). From the "
        "team's updates and the discussion, say where we are, where we need to go, the next "
        f"steps (at most {MAX_NEXT_STEPS}, each with one owner: an employee id from the updates, "
        f'or "person" for the CEO) and the risks. {JSON_ONLY} {{"where_we_are": "...", '
        '"where_we_go": "...", "next_steps": [{"owner": "...", "action": "..."}], "risks": []}'
    )
    lines = ["Updates:"] + [f"[{r.employee_id}, {r.title}]\n{r.text()}" for r in reports]
    if discussion:
        lines += ["", "Discussion:"] + [f"{who}: {text}" for who, text in discussion]
    return system, "\n".join(lines)


def reply_prompt(
    ceo: Member, reports: list[Report], discussion: list[tuple[str, str]], *, scope: str
) -> tuple[str, str]:
    system = (
        f"You are {_who(ceo)}, running the {scope} standup. The CEO (the person) has joined. "
        "Answer them plainly and briefly from the team's updates; say what you will do about it."
    )
    lines = ["Updates:"] + [f"[{r.employee_id}] {r.text()}" for r in reports]
    lines += ["", "Discussion so far:"] + [f"{who}: {text}" for who, text in discussion]
    return system, "\n".join(lines)


def _json(text: str) -> Any:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_update(text: str) -> Report:
    """The model's update. Anything that is not the JSON asked for is kept whole as `done`."""
    data = _json(text)
    if not isinstance(data, dict):
        return Report(employee_id="", done=text.strip())
    return Report(
        employee_id="",
        done=_s(data.get("done")),
        next=_s(data.get("next")),
        blockers=_s(data.get("blockers")),
    )


def parse_summary(text: str, *, owners: set[str]) -> Summary:
    """The co-CEO's summary. Steps for owners that are not in the standup (or the person) are
    dropped; at most MAX_NEXT_STEPS are kept."""
    data = _json(text)
    if not isinstance(data, dict):
        return Summary(where_we_are=text.strip())
    steps: list[NextStep] = []
    for raw in data.get("next_steps") or []:
        if not isinstance(raw, dict):
            continue
        owner, action = _s(raw.get("owner")), _s(raw.get("action"))
        if action and (owner in owners or owner == PERSON):
            steps.append(NextStep(owner=owner, action=action))
    risks = [_s(r) for r in data.get("risks") or [] if _s(r)]
    return Summary(
        where_we_are=_s(data.get("where_we_are")),
        where_we_go=_s(data.get("where_we_go")),
        next_steps=steps[:MAX_NEXT_STEPS],
        risks=risks,
    )


def pick_roster(people: list[Member], department_id: str) -> tuple[Member | None, list[Member]]:
    """(the co-CEO who leads, who reports). A company standup hears from everyone, the co-CEO
    first; a department standup from that department only. Pass active employees only."""
    ceo = next((p for p in people if p.is_co_ceo), None)
    if department_id:
        members = [p for p in people if p.department_id == department_id]
    else:
        members = list(people)
    members.sort(key=lambda p: (not p.is_co_ceo, p.department_id, p.employee_id))
    return ceo, members
