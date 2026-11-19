"""Entry point: one gRPC server on one port, until SIGINT / SIGTERM."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from wfk_platform import configure_logging, setup_telemetry
from wfk_platform.db import Database
from wfk_platform.messaging import run_outbox_relay

from core_python.workforce.app import create_server
from core_python.workforce.settings import Settings

log = logging.getLogger(__name__)

# This service's event stream: it owns every employee.* subject.
STREAM = "EMPLOYEES"
SUBJECTS = ["employee.>"]


async def serve(settings: Settings) -> None:
    configure_logging(settings.service_name, settings.log_level, json_output=settings.log_json)
    telemetry = setup_telemetry(settings)
    db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
    # Do not wait for the database: the service starts, and readiness says when it is usable.
    await db.open(wait=False)
    server, health, port = create_server(settings, db)
    await server.start()
    log.info("started", extra={"port": port})

    stop = asyncio.Event()
    # Publishes employee.* events from the outbox; survives NATS being down.
    relay = asyncio.create_task(
        run_outbox_relay(db, settings.nats_url, stream=STREAM, subjects=SUBJECTS, stop=stop)
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows: Ctrl-C raises instead
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        health.shutdown()  # NOT_SERVING first, so traffic drains
        stop.set()
        await server.stop(grace=5)
        await relay
        await db.close()
        telemetry.shutdown()


def run() -> None:
    settings = Settings()
    # psycopg's async driver needs a selector loop; Windows defaults to Proactor.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(settings), loop_factory=factory)


if __name__ == "__main__":
    run()
