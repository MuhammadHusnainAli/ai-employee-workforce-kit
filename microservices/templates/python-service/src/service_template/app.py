"""Wiring: one gRPC server on one port, with health and (later) this service's API."""

from __future__ import annotations

import grpc

from wfk_platform import GrpcHealth, HealthRegistry, add_health
from wfk_platform.db import Database

from service_template.settings import Settings


def create_server(
    settings: Settings, db: Database, *, port: int | None = None
) -> tuple[grpc.aio.Server, GrpcHealth, int]:
    """gRPC server with the standard health service. Register api/ servicers here.

    Returns the server, its health servicer and the bound port (`port=0` picks a free one).
    """
    registry = HealthRegistry(timeout_s=settings.readiness_timeout_s)
    registry.add("postgres", db.ping)
    server = grpc.aio.server()
    health = add_health(server, registry)
    bound = server.add_insecure_port(f"{settings.host}:{settings.port if port is None else port}")
    return server, health, bound
