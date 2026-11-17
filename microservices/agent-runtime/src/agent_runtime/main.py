"""Entry point: runs the HTTP server (health, REST) and the gRPC server side by side."""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from wfk_platform import configure_logging, setup_telemetry
from wfk_platform.db import Database

from agent_runtime.app import NOT_SERVING, SERVING, create_app, create_grpc_server
from agent_runtime.settings import Settings

log = logging.getLogger(__name__)


async def serve(settings: Settings) -> None:
    configure_logging(settings.service_name, settings.log_level, json_output=settings.log_json)
    telemetry = setup_telemetry(settings)
    db = Database(settings.database_url, tenant_role=settings.db_tenant_role)

    http = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, db),
            host=settings.http_host,
            port=settings.http_port,
            log_config=None,  # keep our JSON logging
        )
    )
    grpc_server, grpc_health, _ = create_grpc_server(settings, db)
    await grpc_server.start()
    await grpc_health.set("", SERVING)
    log.info(
        "started",
        extra={"http_port": settings.http_port, "grpc_port": settings.grpc_port},
    )
    try:
        await http.serve()  # returns on SIGINT / SIGTERM
    finally:
        await grpc_health.set("", NOT_SERVING)
        await grpc_server.stop(grace=5)
        telemetry.shutdown()


async def work(settings: Settings, *, prefix: str, model: str) -> None:
    """Run the Temporal workers (runs, llm, tools queues) until stopped."""
    from contextlib import AsyncExitStack

    from agent_runtime.activities.harness import HarnessActivities
    from agent_runtime.activities.ledger import LedgerActivities
    from agent_runtime.adapters.ledger import Ledger
    from agent_runtime.demo import directory, provider_for, temporal_client
    from agent_runtime.worker import build_workers

    client = await temporal_client(settings)
    db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
    await db.open()
    activities = HarnessActivities(
        employees=directory(model), provider=provider_for(model, settings)
    )
    ledger = LedgerActivities(Ledger(db, f"//{settings.cell_id}/{settings.service_name}"))
    async with AsyncExitStack() as stack:
        for worker in build_workers(client, activities, ledger, prefix):
            await stack.enter_async_context(worker)
        print("ready", flush=True)  # the demo waits for this line
        await asyncio.Event().wait()


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
