"""Fixtures for workflow tests: the local Temporal server (just up), isolated task queues."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest
from agent_harness import FakeProvider
from agent_harness.llm_providers import Provider
from agent_harness.tools import Tool, ToolContext
from temporalio.client import Client, WorkflowHandle

from wfk_platform.db import Database

from agent_runtime.activities.harness import HarnessActivities
from agent_runtime.activities.ledger import LedgerActivities
from agent_runtime.adapters.brain import BrainClient
from agent_runtime.adapters.ledger import Ledger
from agent_runtime.employees import StaticDirectory
from agent_runtime.harness.state import AgentState, EmployeeSpec, Limits, Tier
from agent_runtime.worker import build_workers, connect, queues

TEMPORAL = os.environ.get("WFK_TEST_TEMPORAL", "127.0.0.1:7018")
TENANT = "0193a7c4-0000-7000-8000-000000000001"


class World:
    """Tools with side effects we can count: every call records its idempotency key."""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.sent: list[str] = []

    def tools(self) -> list[Tool]:
        world = self

        def lookup(order: int, ctx: ToolContext) -> str:
            world.keys.append(ctx.state["idempotency_key"])
            return f"order {order}: shipped Tuesday"

        def send_email(to: str, body: str, ctx: ToolContext) -> str:
            world.keys.append(ctx.state["idempotency_key"])
            world.sent.append(to)
            return "sent"

        lookup.__doc__ = "Look an order up.\n\nArgs:\n    order: the order number."
        send_email.__doc__ = "Send an email.\n\nArgs:\n    to: recipient.\n    body: text."
        return [Tool(lookup), Tool(send_email)]

    def employee(self, limits: Limits | None = None, model: str = "fake-1") -> EmployeeSpec:
        tiers: dict[str, Tier] = {"lookup": "R0", "send_email": "R2"}
        return EmployeeSpec.build(
            name="Sam from Support",
            instructions="Answer order questions. Look the order up first.",
            model=model,
            tools=self.tools(),
            tiers=tiers,
            limits=limits or Limits(max_steps=10),
        )


@pytest.fixture
async def client() -> Client:
    try:
        return await connect(TEMPORAL)
    except Exception as exc:
        pytest.skip(f"Temporal not running (just up): {exc}")


def new_id(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@asynccontextmanager
async def running_workers(
    client: Client,
    world: World,
    provider: Provider,
    *,
    employee: Callable[[], EmployeeSpec] | None = None,
    db: Database | None = None,
    brain: BrainClient | None = None,
) -> AsyncIterator[str]:
    """Run all three workers on fresh queues. Yields the `runs` queue to start workflows on."""
    prefix = uuid.uuid4().hex[:8] + "-"
    directory = StaticDirectory({"sam": employee or world.employee})
    activities = HarnessActivities(employees=directory, provider=provider, brain=brain)
    database = db or _shared_db()
    ledger = LedgerActivities(Ledger(database, source="//cell-local/agent-runtime"))
    async with AsyncExitStack() as stack:
        for worker in build_workers(client, activities, ledger, prefix):
            await stack.enter_async_context(worker)
        yield queues(prefix)[0]


def fake(*script: Any, **kw: Any) -> FakeProvider:
    return FakeProvider(list(script), **kw)


async def wait_for(
    handle: WorkflowHandle[Any, Any],
    condition: Callable[[AgentState], bool],
    timeout_s: float = 20,
) -> AgentState:
    """Poll the run's state query until `condition` holds."""
    from agent_runtime.workflows.employee_run import EmployeeRun

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        state = await handle.query(EmployeeRun.state)
        if state is not None and condition(state):
            return state
        if loop.time() > deadline:
            raise AssertionError(f"condition not met; last state: {state}")
        await asyncio.sleep(0.1)


_DB: Database | None = None


def _shared_db() -> Database:
    if _DB is None:
        raise RuntimeError("use the run_db fixture (autouse in workflow tests)")
    return _DB


@pytest.fixture(autouse=True)
def _use_run_db(run_db: Database) -> None:
    global _DB
    _DB = run_db
