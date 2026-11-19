"""Wiring: one gRPC server on one port, with health and RunService."""

from __future__ import annotations

import grpc

from wfk.agent_runtime.v1 import runs_pb2_grpc
from wfk_platform import GrpcHealth, HealthRegistry, add_health
from wfk_platform.db import Database

from core_python.agent_runtime.api.runs import RunServicer
from core_python.agent_runtime.settings import Settings


def create_server(
    settings: Settings, db: Database, *, port: int | None = None
) -> tuple[grpc.aio.Server, GrpcHealth, int]:
    """The service's gRPC server: health (liveness "", readiness "readiness") and RunService.

    Returns the server, its health servicer and the bound port (`port=0` picks a free one).
    """
    registry = HealthRegistry(timeout_s=settings.readiness_timeout_s)
    registry.add("postgres", db.ping)
    server = grpc.aio.server()
    health = add_health(server, registry)
    add_services(server, db)
    bound = server.add_insecure_port(f"{settings.host}:{settings.port if port is None else port}")
    return server, health, bound


def add_services(server: grpc.aio.Server, db: Database) -> None:
    """RunService, on a server that may also carry other modules' services."""
    runs_pb2_grpc.add_RunServiceServicer_to_server(RunServicer(db), server)
