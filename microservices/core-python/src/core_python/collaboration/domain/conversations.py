"""Conversation rules and the call prompt. No I/O."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

#: The participant id of the person who owns the workspace.
PERSON = "person"
#: Questions in one call before it closes.
MAX_CALL_TURNS = 6
#: How long a colleague's answer on a call may be.
MAX_ANSWER_WORDS = 150

SpeakerKind = Literal["employee", "person"]


class Colleague(BaseModel):
    """What a call needs to know about the employee who answers."""

    employee_id: str
    name: str
    title: str = ""
    role: str = ""
    instructions: str = ""
    model: str
    status: str = "active"


def pair_key(a: str, b: str) -> str:
    """The one direct conversation between two participants, whoever starts it."""
    return "|".join(sorted((a, b)))


def speaker(kind: SpeakerKind, actor_id: str) -> tuple[str, SpeakerKind, str]:
    """(participant, kind, author) for whoever calls. Every person speaks as the one person
    participant; the author keeps who it was."""
    if kind == "person":
        return PERSON, "person", actor_id or PERSON
    return actor_id, "employee", actor_id


def trim_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit]) + "…"


def call_prompt(
    callee: Colleague, caller: str, transcript: list[tuple[str, str]], question: str
) -> tuple[str, list[tuple[Literal["user", "assistant"], str]]]:
    """System prompt and turns for the callee's answer. Earlier questions are the caller's
    turns, earlier answers the callee's own."""
    who = f"{callee.name}, {callee.title}" if callee.title else callee.name
    system = (
        f"You are {who}.\n{callee.role.strip()}\n\n{callee.instructions.strip()}\n\n"
        f"Your colleague {caller} is asking you something on a short call. Answer from what you "
        f"know in your role, plainly, in under {MAX_ANSWER_WORDS} words. You cannot use tools "
        "here. If you do not know, say so and say who might."
    )
    turns: list[tuple[Literal["user", "assistant"], str]] = [
        ("user" if kind == "question" else "assistant", text)
        for kind, text in transcript
        if kind in ("question", "answer")
    ]
    turns.append(("user", question))
    return system, turns
