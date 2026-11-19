"""The agent runtime's workers (part `runtime-workers` of core-python), and two commands for
development: `worker` (the workers as their own process, which the demo kills and restarts) and
`demo-run`. In the service, core_python.main runs `run_workers`."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable

from wfk_platform.db import Database

from core_python.agent_runtime.settings import Settings

log = logging.getLogger(__name__)

#: The stream agent-runtime publishes its run.* events to (task-service reads it).
RUNS_STREAM = "RUNS"


async def work(settings: Settings, *, prefix: str, model: str, loops: bool = True) -> None:
    """Run the workers until the process is killed (the demo and crash tests start this as
    its own process and wait for the "ready" line)."""
    await run_workers(
        settings,
        prefix=prefix,
        model=model,
        loops=loops,
        stop=asyncio.Event(),
        on_ready=lambda: print("ready", flush=True),
    )


async def run_workers(
    settings: Settings,
    *,
    prefix: str,
    model: str,
    loops: bool = True,
    stop: asyncio.Event,
    db: Database | None = None,
    on_ready: Callable[[], None] | None = None,
) -> None:
    """Run the Temporal workers (runs, llm, tools queues) until `stop` is set, with the run
    outbox relay (run.* to the RUNS stream) and the task inbox (task.* starts and steers runs).

    `db` is run_db; when given it is shared and stays open, otherwise this opens its own."""
    from contextlib import AsyncExitStack

    from wfk_platform.messaging import run_inbox_consumer, run_outbox_relay

    from core_python.agent_runtime.activities.harness import HarnessActivities
    from core_python.agent_runtime.activities.ledger import LedgerActivities
    from core_python.agent_runtime.adapters import tasks
    from core_python.agent_runtime.adapters.ledger import Ledger
    from core_python.agent_runtime.demo import directory, provider_for, temporal_client
    from core_python.agent_runtime.worker import build_workers, queues

    client = await temporal_client(settings)
    own_db = db is None
    if db is None:
        db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
        await db.open()
    from core_python.agent_runtime.adapters.brain import BrainClient

    brain = (
        BrainClient(settings.brain_address, valkey_url=settings.valkey_url or None)
        if settings.brain_address
        else None
    )
    from core_python.agent_runtime.employees import (
        ChainDirectory,
        EmployeeDirectory,
        WorkforceDirectory,
    )
    from core_python.agent_runtime.tool_catalog import CATALOG

    # Published employees first; the employees defined in code (the demo) after.
    employees: EmployeeDirectory = directory(model)
    if settings.workforce_address:
        employees = ChainDirectory(
            WorkforceDirectory(settings.workforce_address, CATALOG), employees
        )
    activities = HarnessActivities(
        employees=employees, provider=provider_for(model, settings), brain=brain
    )
    ledger = LedgerActivities(Ledger(db, f"//{settings.cell_id}/{settings.service_name}"))
    # An evaluation worker (--no-loops) runs workflows only: it must never take live tasks.
    # Its runs' events still reach NATS: the live worker's relay publishes the shared outbox.
    running: list[asyncio.Task[None]] = []
    if loops:
        running = [
            asyncio.create_task(
                run_outbox_relay(
                    db, settings.nats_url, stream=RUNS_STREAM, subjects=["run.>"], stop=stop
                )
            ),
            asyncio.create_task(
                run_inbox_consumer(
                    db,
                    settings.nats_url,
                    stream=tasks.STREAM,
                    consumer=tasks.CONSUMER,
                    subject=tasks.SUBJECT,
                    handler=tasks.TaskCommands(client, queues(prefix)[0]),
                    stop=stop,
                )
            ),
        ]
    try:
        async with AsyncExitStack() as stack:
            for worker in build_workers(client, activities, ledger, prefix):
                await stack.enter_async_context(worker)
            log.info("workers started", extra={"prefix": prefix, "loops": loops})
            if on_ready is not None:
                on_ready()
            await stop.wait()
    finally:
        stop.set()
        await asyncio.gather(*running, return_exceptions=True)
        if own_db:
            await db.close()


def run(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="agent-runtime")
    sub = parser.add_subparsers(dest="cmd")
    worker = sub.add_parser("worker", help="Temporal workers")
    worker.add_argument("--prefix", default="", help="task queue prefix")
    worker.add_argument("--model", default="local-mock", help="scripted, local-mock, claude-haiku")
    worker.add_argument(
        "--no-loops", action="store_true", help="workflows only: no outbox relay, no task inbox"
    )
    demo = sub.add_parser("demo-run", help="a durable demo run you can watch")
    demo.add_argument("text", help="the text to summarise")
    demo.add_argument("--model", default="scripted", help="scripted, local-mock, claude-haiku")
    demo.add_argument("--kill-worker-after-step", type=int, default=None, metavar="SEQ")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.cmd == "worker":
        main = work(settings, prefix=args.prefix, model=args.model, loops=not args.no_loops)
    elif args.cmd == "demo-run":
        from core_python.agent_runtime.demo import run_demo

        main = run_demo(
            args.text,
            model=args.model,
            kill_worker_after_step=args.kill_worker_after_step,
            settings=settings,
        )
    else:
        parser.print_help()
        return
    # psycopg's async driver needs a selector loop; Windows defaults to Proactor.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(main, loop_factory=factory)  # type: ignore[arg-type]


if __name__ == "__main__":
    run()
