"""Phase 2 exit: the demo run survives a worker kill and shows its steps (just up)."""

import pytest

from agent_runtime.demo import run_demo
from agent_runtime.settings import Settings

from ..conftest import RUN_DB

pytestmark = pytest.mark.integration


async def test_demo_run_survives_a_worker_kill() -> None:
    lines: list[str] = []
    outcome = await run_demo(
        "Durable runs survive crashes. Nothing is lost.",
        kill_worker_after_step=0,
        settings=Settings(database_url=RUN_DB),
        say=lines.append,
    )
    assert outcome.state.status == "completed"
    assert outcome.state.output == (
        "[scripted, not an LLM] 7 words. Gist: Durable runs survive crashes."
    )
    assert any("killed the worker" in line for line in lines)
    assert any("step 0: tool_calls" in line for line in lines)
    assert any("step 1: final" in line for line in lines)
