"""Entry point: one gRPC server on one port (health included), plus subcommands."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from wfk_platform import configure_logging, setup_telemetry
from wfk_platform.db import Database

from agent_runtime.app import create_server
from agent_runtime.settings import Settings

log = logging.getLogger(__name__)

#: The stream agent-runtime publishes its run.* events to (task-service reads it).
RUNS_STREAM = "RUNS"


async def serve(settings: Settings) -> None:
    """One gRPC server on one port (health included), until SIGINT / SIGTERM."""
    configure_logging(settings.service_name, settings.log_level, json_output=settings.log_json)
    telemetry = setup_telemetry(settings)
    db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
    # Do not wait for the database: the service starts, and readiness says when it is usable.
    await db.open(wait=False)
    server, health, port = create_server(settings, db, port=settings.port)
    await server.start()
    log.info("started", extra={"port": port})

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows: Ctrl-C raises instead
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        health.shutdown()  # NOT_SERVING first, so traffic drains
        await server.stop(grace=5)
        await db.close()
        telemetry.shutdown()


async def work(settings: Settings, *, prefix: str, model: str) -> None:
    """Run the Temporal workers (runs, llm, tools queues) until stopped, with the run outbox
    relay (run.* to the RUNS stream) and the task inbox (task.* starts and steers runs)."""
    from contextlib import AsyncExitStack

    from wfk_platform.messaging import run_inbox_consumer, run_outbox_relay

    from agent_runtime.activities.harness import HarnessActivities
    from agent_runtime.activities.ledger import LedgerActivities
    from agent_runtime.adapters import tasks
    from agent_runtime.adapters.ledger import Ledger
    from agent_runtime.demo import directory, provider_for, temporal_client
    from agent_runtime.worker import build_workers, queues

    client = await temporal_client(settings)
    db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
    await db.open()
    activities = HarnessActivities(
        employees=directory(model), provider=provider_for(model, settings)
    )
    ledger = LedgerActivities(Ledger(db, f"//{settings.cell_id}/{settings.service_name}"))
    stop = asyncio.Event()
    loops = [
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
            print("ready", flush=True)  # the demo waits for this line
            await asyncio.Event().wait()
    finally:
        stop.set()
        await asyncio.gather(*loops, return_exceptions=True)
        await db.close()


def run(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="agent-runtime")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="HTTP and gRPC servers (the default)")
    worker = sub.add_parser("worker", help="Temporal workers")
    worker.add_argument("--prefix", default="", help="task queue prefix")
    worker.add_argument("--model", default="local-mock", help="scripted, local-mock, claude-haiku")
    demo = sub.add_parser("demo-run", help="a durable demo run you can watch")
    demo.add_argument("text", help="the text to summarise")
    demo.add_argument("--model", default="scripted", help="scripted, local-mock, claude-haiku")
    demo.add_argument("--kill-worker-after-step", type=int, default=None, metavar="SEQ")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.cmd == "worker":
        main = work(settings, prefix=args.prefix, model=args.model)
    elif args.cmd == "demo-run":
        from agent_runtime.demo import run_demo

        main = run_demo(
            args.text,
            model=args.model,
            kill_worker_after_step=args.kill_worker_after_step,
            settings=settings,
        )
    else:
        main = serve(settings)
    # psycopg's async driver needs a selector loop; Windows defaults to Proactor.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(main, loop_factory=factory)  # type: ignore[arg-type]


if __name__ == "__main__":
    run()
