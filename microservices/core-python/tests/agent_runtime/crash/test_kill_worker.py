"""Kill the worker mid-run: a new one finishes the run and no tool effect happens twice.

The worker runs as its own OS process. The test kills it (no shutdown, no cleanup) while a
tool is half-way through a write, then starts a fresh worker process. Temporal notices the
dead worker through the act activity's 10 s heartbeat timeout and retries the activity on the
new worker, with the same idempotency key.
"""

import asyncio
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest

from wfk_platform.db import Database

from core_python.agent_runtime.worker import connect, queues
from core_python.agent_runtime.workflows.employee_run import EmployeeRun
from core_python.agent_runtime.workflows.types import RunInput

from ..conftest import RUN_DB, TENANT

pytestmark = pytest.mark.integration
WORKER = Path(__file__).with_name("worker_proc.py")


def start_worker(prefix: str) -> subprocess.Popen[str]:
    proc = subprocess.Popen(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, str(WORKER), prefix, RUN_DB],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line.strip() == "ready", f"worker did not start: {line!r}"
    return proc


def effects() -> dict[int, tuple[str, int]]:
    """n -> (idempotency key, attempts)."""
    with psycopg.connect(RUN_DB) as c:
        rows = c.execute("SELECT n, key, attempts FROM crash_effects").fetchall()
    return {n: (key, attempts) for n, key, attempts in rows}


async def wait_until(condition, timeout_s: float = 30) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.1)


async def test_killed_worker_is_replaced_without_repeating_a_tool_effect(run_db: Database) -> None:
    with psycopg.connect(RUN_DB, autocommit=True) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS crash_effects"
            " (key text PRIMARY KEY, n int NOT NULL, attempts int NOT NULL)"
        )
        c.execute("TRUNCATE crash_effects")
    try:
        client = await connect("127.0.0.1:7018")
    except Exception as exc:
        pytest.skip(f"Temporal not running (just up): {exc}")

    prefix = f"crash-{uuid.uuid4().hex[:8]}-"
    first = start_worker(prefix)
    run_id = f"crash-{uuid.uuid4().hex[:10]}"
    handle = await client.start_workflow(
        EmployeeRun.run,
        RunInput(run_id=run_id, tenant_id=TENANT, employee_id="writer", task="Write 3 records."),
        id=run_id,
        task_queue=queues(prefix)[0],
        execution_timeout=timedelta(seconds=120),
    )

    second = None
    try:
        # Record 1's write has started (and sleeps): kill the worker right there.
        await wait_until(lambda: 1 in effects())
        first.kill()
        first.wait(timeout=10)

        second = start_worker(prefix)
        outcome = await handle.result()
    finally:
        for proc in (first, second):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

    assert (outcome.state.status, outcome.state.output) == ("completed", "Wrote 3 records.")

    done = effects()
    assert sorted(done) == [0, 1, 2], "every record written"
    assert done[0][1] == 1, "the write finished before the crash never ran again"
    assert done[2][1] == 1
    assert done[1][1] == 2, "the interrupted write was retried..."
    assert done[1][0] == f"{run_id}:1:0", "...with the same idempotency key"

    async with run_db.tenant_tx(TENANT) as conn:
        cur = await conn.execute(
            "SELECT seq, kind FROM run_steps WHERE run_id = %s ORDER BY seq", (run_id,)
        )
        ledger = [(r["seq"], r["kind"]) for r in await cur.fetchall()]
    assert ledger == [(0, "tool_calls"), (1, "tool_calls"), (2, "tool_calls"), (3, "final")]
