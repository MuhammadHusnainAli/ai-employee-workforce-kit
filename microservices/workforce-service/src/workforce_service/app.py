"""Wiring: one gRPC server on one port, with health and WorkforceService."""

from __future__ import annotations

import grpc

from wfk.workforce_service.v1 import workforce_pb2_grpc
from wfk_platform import GrpcHealth, HealthRegistry, add_health
from wfk_platform.db import Database

from workforce_service.adapters.postgres.employees import Employees
from workforce_service.api.workforce import WorkforceServicer
from workforce_service.settings import Settings


def create_server(
    settings: Settings, db: Database, *, port: int | None = None
) -> tuple[grpc.aio.Server, GrpcHealth, int]:
    """gRPC server: the standard health service and WorkforceService.

    Returns the server, its health servicer and the bound port (`port=0` picks a free one).
    """
    registry = HealthRegistry(timeout_s=settings.readiness_timeout_s)
    registry.add("postgres", db.ping)
    server = grpc.aio.server()
    health = add_health(server, registry)
    source = f"//{settings.cell_id}/{settings.service_name}"
    workforce_pb2_grpc.add_WorkforceServiceServicer_to_server(
        WorkforceServicer(Employees(db, source)), server
    )
    bound = server.add_insecure_port(f"{settings.host}:{settings.port if port is None else port}")
    return server, health, bound
