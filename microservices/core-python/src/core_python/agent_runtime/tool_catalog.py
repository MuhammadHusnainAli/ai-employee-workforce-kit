"""The tools this runtime can execute, by name: what an employee version's tool grants refer to.

Tools that reach outside (email, CRM, calendars) arrive with the Integration Service (plan 4.3);
until then a grant for them is skipped and logged. These run in-process and touch nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from agent_harness.tools import Tool


def word_count(text: str) -> int:
    """Count the words in a text.

    Args:
        text: the text to count.
    """
    return len(text.split())


def char_count(text: str) -> int:
    """Count the characters in a text, spaces included.

    Args:
        text: the text to count.
    """
    return len(text)


def current_time() -> str:
    """The current date and time in UTC, ISO 8601."""
    return datetime.now(UTC).isoformat(timespec="seconds")


#: name -> factory. A fresh Tool per spec, like a fresh worker would build.
CATALOG: dict[str, Callable[[], Tool]] = {
    "word_count": lambda: Tool(word_count),
    "char_count": lambda: Tool(char_count),
    "current_time": lambda: Tool(current_time),
}
