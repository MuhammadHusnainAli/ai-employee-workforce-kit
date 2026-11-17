"""A worker process for the crash test: the test kills it mid-run and starts another.

    python worker_proc.py <queue-prefix> <run-db-url>

Its employee writes to `crash_effects`, keyed by the idempotency key, the way a real connector
must: the first write applies the effect, a repeat only counts the attempt. The scripted model
reads the conversation to decide what to do next, so a fresh process answers exactly as the
dead one would have.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from typing import Any

import psycopg
from agent_harness import FakeProvider
from agent_harness.llm_providers import CompletionRequest
from agent_harness.tools import Tool, ToolContext
from agent_harness.types import ToolResultBlock, ToolUseBlock

from wfk_platform.db import Database

from agent_runtime.activities.harness import HarnessActivities
from agent_runtime.activities.ledger import LedgerActivities
from agent_runtime.adapters.ledger import Ledger
from agent_runtime.employees import StaticDirectory
from agent_runtime.harness.state import EmployeeSpec, Limits
from agent_runtime.worker import build_workers, connect

WRITES = 3  # the employee writes three records, one per step, then answers
SLOW_STEP = 1  # the write in this step takes long enough to be killed mid-way


def model(req: CompletionRequest) -> Any:
    done = sum(isinstance(b, ToolResultBlock) for m in req.messages for b in m.content)
    if done < WRITES:
        return ToolUseBlock(id=f"w{done}", name="write_record", input={"n": done})
    return f"Wrote {WRITES} records."


def employee(run_db: str) -> EmployeeSpec:
    async def write_record(n: int, ctx: ToolContext) -> str:
        key = ctx.state["idempotency_key"]
        async with await psycopg.AsyncConnection.connect(run_db, autocommit=True) as conn:
            await conn.execute(
                "INSERT INTO crash_effects (key, n, attempts) VALUES (%s, %s, 1)"
                " ON CONFLICT (key) DO UPDATE SET attempts = crash_effects.attempts + 1",
                (key, n),
            )
        if n == SLOW_STEP:
            await asyncio.sleep(4)  # the test kills the worker during this sleep
        return f"record {n} written"

    write_record.__doc__ = "Write a record.\n\nArgs:\n    n: which record."
    return EmployeeSpec.build(
        name="Writer",
        instructions="Write the records, then report.",
        model="fake-1",
        tools=[Tool(write_record)],
        tiers={"write_record": "R1"},
        limits=Limits(max_steps=10),
    )


async def main(prefix: str, run_db: str) -> None:
    client = await connect("127.0.0.1:7001")
    db = Database(run_db, tenant_role="wfk_app")
    await db.open()
    activities = HarnessActivities(
        employees=StaticDirectory({"writer": lambda: employee(run_db)}),
        provider=FakeProvider([model], loop=True),
    )
    ledger = LedgerActivities(Ledger(db, "//cell-local/agent-runtime"))
    async with AsyncExitStack() as stack:
        for worker in build_workers(client, activities, ledger, prefix):
            await stack.enter_async_context(worker)
        print("ready", flush=True)
        await asyncio.Event().wait()  # run until killed


if __name__ == "__main__":
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(main(sys.argv[1], sys.argv[2]), loop_factory=factory)
