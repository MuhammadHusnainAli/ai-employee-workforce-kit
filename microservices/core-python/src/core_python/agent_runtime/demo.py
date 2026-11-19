"""The Phase 2 demo: a durable agent run you can watch, and kill a worker in the middle of.

    just demo-run "Some text to summarise."
    just demo-run "Some text." --kill-worker-after-step 0    # kill the worker, watch it resume
    just demo-run "Some text." --model claude-haiku          # real model: key in deployments/.env

The worker runs as its own process, so killing it is a real crash: no shutdown, no cleanup.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from agent_harness import FakeProvider
from agent_harness.llm_providers import CompletionRequest, Provider
from agent_harness.tools import Tool
from agent_harness.types import ModelResponse, ToolResultBlock, ToolUseBlock

from wfk_platform.db import Database
from wfk_platform.migrate import Runner, ensure_database

from core_python.agent_runtime.adapters.model_gateway import gateway_provider
from core_python.agent_runtime.claim_check import ClaimCheckCodec, S3BlobStore
from core_python.agent_runtime.employees import StaticDirectory
from core_python.agent_runtime.harness.state import EmployeeSpec, Limits
from core_python.agent_runtime.settings import Settings
from core_python.agent_runtime.tool_catalog import word_count
from core_python.agent_runtime.worker import connect, queues
from core_python.agent_runtime.workflows.employee_run import EmployeeRun
from core_python.agent_runtime.workflows.types import RunInput, RunOutcome

DEMO_TENANT = "0193a7c4-0000-7000-8000-00000000d3e0"
SCRIPTED = "scripted"


# ---- the demo employee -------------------------------------------------------------------


def summariser(model: str) -> EmployeeSpec:
    return EmployeeSpec.build(
        name="Sam the Summariser",
        instructions=(
            "Summarise the text the user gives you in one or two sentences. "
            "Call word_count on the text first and mention the count."
        ),
        model=model,
        tools=[Tool(word_count)],
        tiers={"word_count": "R0"},
        limits=Limits(max_steps=6, max_cost_usd=0.25),
    )


def directory(model: str) -> StaticDirectory:
    return StaticDirectory({"summariser": lambda: summariser(model)})


def scripted_model(req: CompletionRequest) -> Any:
    """Not an LLM: a fixed two-step script that reads the conversation, so any worker process
    gives the same answer. It counts the words, then returns the first sentence."""
    task = next(m.text for m in req.messages if m.role == "user")
    counted = [b for m in req.messages for b in m.content if isinstance(b, ToolResultBlock)]
    if not counted:
        return ToolUseBlock(id="count-1", name="word_count", input={"text": task})
    count = re.sub(r"<[^>]+>", "", counted[-1].content).strip()
    first = re.split(r"(?<=[.!?])\s+", task.strip())[0]
    return f"[scripted, not an LLM] {count} words. Gist: {first}"


class PacedProvider(FakeProvider):
    """The scripted model, taking a moment per call the way a real model does. That gives the
    demo a dependable window to kill the worker while a model call is in flight."""

    def __init__(self, pace_s: float) -> None:
        super().__init__([scripted_model], loop=True)
        self.pace_s = pace_s

    async def complete(self, req: CompletionRequest) -> ModelResponse:
        await asyncio.sleep(self.pace_s)
        return await super().complete(req)


def provider_for(model: str, settings: Settings) -> Provider:
    if model == SCRIPTED:
        return PacedProvider(pace_s=1.5)
    return gateway_provider(settings)


async def temporal_client(settings: Settings) -> Any:
    store = S3BlobStore(
        endpoint=settings.s3_endpoint,
        bucket=settings.payload_bucket,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
    )
    codec = ClaimCheckCodec(store, threshold=settings.claim_check_threshold_bytes)
    return await connect(settings.temporal_address, settings.temporal_namespace, codec=codec)


# ---- the demo run ------------------------------------------------------------------------


def _spawn_worker(prefix: str, model: str, settings: Settings) -> subprocess.Popen[str]:
    """Start a worker process that uses the same database as this demo."""
    cmd = [sys.executable, "-m", "core_python.agent_runtime.main", "worker", "--prefix", prefix]
    cmd += ["--model", model]
    env = {**os.environ, "WFK_DATABASE_URL": settings.database_url}
    proc = subprocess.Popen(  # noqa: S603 - our own module, fixed arguments
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env
    )
    assert proc.stdout is not None
    if proc.stdout.readline().strip() != "ready":
        proc.kill()
        raise RuntimeError("the worker did not start (is the stack up? just up at the repo root)")
    return proc


def _stop(proc: subprocess.Popen[str] | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)


async def _ledger(db: Database, run_id: str, after: int) -> list[dict[str, Any]]:
    async with db.tenant_tx(DEMO_TENANT) as conn:
        cur = await conn.execute(
            "SELECT seq, step, kind, status_after, model, input_tokens, output_tokens"
            " FROM run_steps WHERE run_id = %s AND seq > %s ORDER BY seq",
            (run_id, after),
        )
        return list(await cur.fetchall())


async def run_demo(
    text: str,
    *,
    model: str = SCRIPTED,
    kill_worker_after_step: int | None = None,
    settings: Settings | None = None,
    say: Any = print,
) -> RunOutcome:
    settings = settings or Settings()
    ensure_database(settings.database_url)
    Runner(settings.database_url, migrations_dir()).up()
    db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
    await db.open()

    prefix = f"demo-{uuid.uuid4().hex[:6]}-"
    worker = _spawn_worker(prefix, model, settings)
    say(f"worker started (pid {worker.pid}), model: {model}")
    client = await temporal_client(settings)
    run_id = f"demo-{uuid.uuid4().hex[:10]}"
    handle = await client.start_workflow(
        EmployeeRun.run,
        RunInput(run_id=run_id, tenant_id=DEMO_TENANT, employee_id="summariser", task=text),
        id=run_id,
        task_queue=queues(prefix)[0],
        execution_timeout=timedelta(minutes=5),
    )
    say(f"run {run_id} started")
    seen, killed = -1, False
    try:
        result = asyncio.ensure_future(handle.result())
        while True:
            for row in await _ledger(db, run_id, seen):
                seen = row["seq"]
                say(
                    f"  step {row['seq']}: {row['kind']:<11} -> {row['status_after']:<13}"
                    f" model={row['model'] or '-'} tokens={row['input_tokens']}"
                    f"/{row['output_tokens']}"
                )
                if kill_worker_after_step == row["seq"] and not killed and not result.done():
                    _stop(worker)
                    killed = True
                    say(
                        f"  ** killed the worker (pid {worker.pid}) without warning, "
                        "in the middle of the next model call **"
                    )
                    worker = _spawn_worker(prefix, model, settings)
                    say(
                        f"  ** started a new worker (pid {worker.pid}); Temporal notices the "
                        "dead one by its missed heartbeats (15 s) and the run carries on **"
                    )
            if result.done():
                break
            await asyncio.sleep(0.2)
        outcome: RunOutcome = result.result()
        for row in await _ledger(db, run_id, seen):  # anything recorded after the last poll
            say(f"  step {row['seq']}: {row['kind']:<11} -> {row['status_after']}")
        if kill_worker_after_step is not None and not killed:
            say(f"  (the run finished before step {kill_worker_after_step}: nothing to kill)")
    finally:
        _stop(worker)
        await db.close()

    state = outcome.state
    say(f"run {state.status} ({state.stop_reason}) after {outcome.ledger_entries} ledger entries")
    say(f"answer: {state.output}")
    return outcome


def migrations_dir() -> Path:
    """This service's migrations (the demo runs from a source checkout)."""
    return Path(__file__).resolve().parents[3] / "migrations" / "agent_runtime"
