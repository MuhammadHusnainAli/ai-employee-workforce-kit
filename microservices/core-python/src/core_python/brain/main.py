"""Entry point: brain-api (Recall over gRPC, one port) or brain-ingest (the write path)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from wfk_platform import configure_logging, setup_telemetry
from wfk_platform.db import Database

from core_python.brain.adapters.cache.pinned import NoCache, PinnedCache, ValkeyPinnedCache
from core_python.brain.adapters.modelgw.embeddings import Embedder, GatewayEmbedder, HashEmbedder
from core_python.brain.app import create_server
from core_python.brain.recall import Recall
from core_python.brain.settings import Settings

log = logging.getLogger(__name__)


def pinned_cache(settings: Settings) -> PinnedCache:
    return ValkeyPinnedCache(settings.valkey_url) if settings.valkey_url else NoCache()


def embedder(settings: Settings) -> Embedder:
    if settings.embedder == "gateway":
        return GatewayEmbedder(
            settings.model_gateway_url, settings.model_gateway_key, settings.embedding_model
        )
    return HashEmbedder()


async def serve(settings: Settings) -> None:
    """One gRPC server on one port (health included), until SIGINT / SIGTERM."""
    configure_logging(settings.service_name, settings.log_level, json_output=settings.log_json)
    telemetry = setup_telemetry(settings)
    db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
    # Do not wait for the database: the service starts, and readiness says when it is usable.
    await db.open(wait=False)
    server, health, port = create_server(
        settings, Recall(db, embedder(settings), pinned_cache(settings)), port=settings.port
    )
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


def run(argv: list[str] | None = None) -> None:
    """`brain-service` (or `serve`): brain-api, Recall over gRPC.
    `brain-service ingest`: brain-ingest, the write path (Temporal worker, run events)."""
    import argparse

    parser = argparse.ArgumentParser(prog="brain-service")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="brain-api: Recall over gRPC (the default)")
    sub.add_parser("ingest", help="brain-ingest: remember finished runs")
    args = parser.parse_args(argv)
    settings = Settings()
    if args.cmd == "ingest":
        from core_python.brain.ingest import run_ingest

        main = run_ingest(settings)
    else:
        main = serve(settings)
    # psycopg's async driver needs a selector loop; Windows defaults to Proactor.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main, loop_factory=factory)


if __name__ == "__main__":
    run()
