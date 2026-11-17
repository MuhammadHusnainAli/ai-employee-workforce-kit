"""Wiring: builds the HTTP app and the gRPC server from settings."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import grpc
from fastapi import FastAPI
from grpc_health.v1 import health_pb2, health_pb2_grpc

# The async servicer published as grpc_health.v1.health.aio; imported directly so type
# checkers can see it.
from grpc_health.v1._async import HealthServicer

from wfk_platform import HealthRegistry, health_router
from wfk_platform.db import Database

from service_template.settings import Settings


def create_app(settings: Settings, db: Database) -> FastAPI:
    """HTTP app: health endpoints now, REST routes from api/ later."""
    registry = HealthRegistry(timeout_s=settings.readiness_timeout_s)
    registry.add("postgres", db.ping)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Do not wait for the database: the service starts, and /readyz says when it is usable.
        await db.open(wait=False)
        try:
            yield
        finally:
            await db.close()

    app = FastAPI(title=settings.service_name, lifespan=lifespan)
    app.include_router(health_router(registry))
    return app


def create_grpc_server(
    settings: Settings, *, port: int | None = None
) -> tuple[grpc.aio.Server, HealthServicer, int]:
    """gRPC server with the standard health service. Register api/ servicers here.

    Returns the server, its health servicer and the bound port (`port=0` picks a free one).
    """
    server = grpc.aio.server()
    health_servicer = HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    want = settings.grpc_port if port is None else port
    bound = server.add_insecure_port(f"{settings.http_host}:{want}")
    return server, health_servicer, bound


SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING
