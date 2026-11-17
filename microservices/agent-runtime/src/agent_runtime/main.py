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


def run() -> None:
    settings = Settings()
    # psycopg's async driver needs a selector loop; Windows defaults to Proactor.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(serve(settings), loop_factory=factory)


if __name__ == "__main__":
    run()
