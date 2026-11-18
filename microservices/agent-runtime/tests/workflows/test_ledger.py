"""The run ledger: atomic, idempotent, complete (just up)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from agent_harness.types import ToolUseBlock
from temporalio.client import Client

from wfk_platform.db import Database

import agent_runtime.adapters.ledger as ledger_module
from agent_runtime.adapters.ledger import Ledger
from agent_runtime.workflows.employee_run import EmployeeRun
from agent_runtime.workflows.types import ApprovalAnswer, LedgerEntry, RunInput, StartRecord

from .conftest import TENANT, World, fake, new_id, running_workers, wait_for

pytestmark = pytest.mark.integration
SOURCE = "//cell-local/agent-runtime"


def entry(run_id: str, seq: int, **kw: Any) -> LedgerEntry:
    base: dict[str, Any] = {
        "run_id": run_id,
        "tenant_id": TENANT,
        "employee_id": "sam",
        "seq": seq,
        "kind": "tool_calls",
        "step": seq + 1,
        "status": "running",
    }
    base.update(kw)
    return LedgerEntry(**base)


async def rows(db: Database, sql: str, *args: Any) -> list[dict[str, Any]]:
    async with db.tenant_tx(TENANT) as conn:
        cur = await conn.execute(sql, args)  # type: ignore[arg-type]
        return list(await cur.fetchall())


async def events(db: Database, run_id: str) -> list[str]:
    async with db.tx() as conn:
        cur = await conn.execute(
            "SELECT type FROM outbox WHERE subject = %s ORDER BY seq", (run_id,)
        )
        return [r["type"] for r in await cur.fetchall()]


async def started(db: Database) -> tuple[Ledger, str]:
    led = Ledger(db, SOURCE)
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    assert await led.start(
        StartRecord(run_id=run_id, tenant_id=TENANT, employee_id="sam", task="x")
    )
    return led, run_id


async def test_start_is_idempotent(run_db: Database) -> None:
    led, run_id = await started(run_db)
    again = StartRecord(run_id=run_id, tenant_id=TENANT, employee_id="sam", task="x")
    assert not await led.start(again)
    assert await events(run_db, run_id) == ["run.started"]


async def test_a_retried_write_adds_nothing(run_db: Database) -> None:
    """A worker that committed the entry but died before reporting it gets the activity
    retried. The retry must not add a row or an event."""
    led, run_id = await started(run_db)
    assert await led.record(entry(run_id, 0))
    assert not await led.record(entry(run_id, 0))
    assert len(await rows(run_db, "SELECT seq FROM run_steps WHERE run_id = %s", run_id)) == 1
    assert await events(run_db, run_id) == ["run.started", "run.step_completed"]


async def test_entry_and_events_commit_together(
    run_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    led, run_id = await started(run_db)

    async def outbox_down(*_: Any, **__: Any) -> None:
        raise ConnectionError("outbox write failed")

    monkeypatch.setattr(ledger_module, "enqueue", outbox_down)
    with pytest.raises(ConnectionError):
        await led.record(entry(run_id, 0, status="completed"))
    monkeypatch.undo()

    assert await rows(run_db, "SELECT seq FROM run_steps WHERE run_id = %s", run_id) == []
    (run,) = await rows(run_db, "SELECT status, steps FROM runs WHERE run_id = %s", run_id)
    assert (run["status"], run["steps"]) == ("running", 0), "nothing half-written"
    # The retry, with the outbox back, writes everything once.
    assert await led.record(entry(run_id, 0, status="completed"))
    assert await events(run_db, run_id) == ["run.started", "run.step_completed", "run.completed"]


async def test_a_whole_run_is_in_the_ledger(client: Client, run_db: Database) -> None:
    world = World()
    provider = fake(
        ToolUseBlock(id="c1", name="lookup", input={"order": 42}),
        ToolUseBlock(id="c2", name="send_email", input={"to": "ana@example.com", "body": "Tue"}),
        "Done: Ana knows.",
    )
    async with running_workers(client, world, provider) as queue:
        inp = RunInput(run_id=new_id(), tenant_id=TENANT, employee_id="sam", task="Tell Ana.")
        handle = await client.start_workflow(
            EmployeeRun.run,
            inp,
            id=inp.run_id,
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
        await wait_for(handle, lambda s: s.stop_reason == "approval")
        await handle.signal(EmployeeRun.approve, ApprovalAnswer(approved=True, by="maria"))
        outcome = await handle.result()

    steps = await rows(
        run_db,
        "SELECT seq, kind, status_after, prompt_hash, results FROM run_steps"
        " WHERE run_id = %s ORDER BY seq",
        inp.run_id,
    )
    assert [(s["seq"], s["kind"], s["status_after"]) for s in steps] == [
        (0, "tool_calls", "running"),
        (1, "ask_human", "waiting_human"),
        (2, "approved", "running"),
        (3, "final", "completed"),
    ]
    assert outcome.ledger_entries == 4
    assert all(len(s["prompt_hash"]) == 64 for s in steps if s["kind"] != "approved")
    assert steps[2]["results"][0]["idempotency_key"] == f"{inp.run_id}:1:0"

    (run,) = await rows(
        run_db, "SELECT status, stop_reason, output, steps FROM runs WHERE run_id = %s", inp.run_id
    )
    assert (run["status"], run["stop_reason"], run["output"]) == (
        "completed",
        "final",
        "Done: Ana knows.",
    )
    assert await events(run_db, inp.run_id) == [
        "run.started",
        *["run.step_completed"] * 4,
        "run.completed",
    ]


async def test_terminal_event_carries_the_task_and_output(run_db: Database) -> None:
    """brain-ingest remembers a run from run.completed alone."""
    led, run_id = await started(run_db)
    long_output = "y" * 20_000
    assert await led.record(entry(run_id, 0, status="completed", output=long_output))
    async with run_db.tx() as conn:
        cur = await conn.execute(
            "SELECT data FROM outbox WHERE subject = %s AND type = 'run.completed'", (run_id,)
        )
        row = await cur.fetchone()
    assert row is not None
    data = row["data"]
    assert data["task"] == "x" and data["employee_id"] == "sam"
    assert len(data["output"]) == ledger_module.EVENT_TEXT_LIMIT, "clipped, so events stay small"


async def test_the_mode_is_recorded_and_travels_with_every_event(run_db: Database) -> None:
    """Evaluation and shadow runs say so, so no consumer mistakes them for real work."""
    led = Ledger(run_db, SOURCE)
    run_id = f"eval-{uuid.uuid4().hex[:10]}"
    rec = StartRecord(run_id=run_id, tenant_id=TENANT, employee_id="sam", task="x",
                      mode="eval", candidate="p-1")  # fmt: skip
    assert await led.start(rec)
    assert await led.record(entry(run_id, 0, status="completed", mode="eval"))
    (run,) = await rows(run_db, "SELECT mode, candidate FROM runs WHERE run_id = %s", run_id)
    assert (run["mode"], run["candidate"]) == ("eval", "p-1")
    async with run_db.tx() as conn:
        cur = await conn.execute(
            "SELECT type, data->>'mode' AS mode FROM outbox WHERE subject = %s ORDER BY seq",
            (run_id,),
        )
        events = [(r["type"], r["mode"]) for r in await cur.fetchall()]
    assert events == [
        ("run.started", "eval"),
        ("run.step_completed", "eval"),
        ("run.completed", "eval"),
    ]
