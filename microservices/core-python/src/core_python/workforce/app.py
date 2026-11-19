"""Wiring: one gRPC server on one port, with health and WorkforceService."""

from __future__ import annotations

import grpc

from wfk.workforce_service.v1 import workforce_pb2_grpc
from wfk_platform import GrpcHealth, HealthRegistry, add_health
from wfk_platform.db import Database

from core_python.workforce.adapters.postgres.employees import Employees
from core_python.workforce.api.workforce import WorkforceServicer
from core_python.workforce.settings import Settings

# This module's event stream: it owns every employee.* subject.
STREAM = "EMPLOYEES"
SUBJECTS = ["employee.>"]


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
    add_services(server, settings, db)
    bound = server.add_insecure_port(f"{settings.host}:{settings.port if port is None else port}")
    return server, health, bound


def add_services(server: grpc.aio.Server, settings: Settings, db: Database) -> None:
    """WorkforceService, on a server that may also carry other modules' services."""
    source = f"//{settings.cell_id}/{settings.service_name}"
    workforce_pb2_grpc.add_WorkforceServiceServicer_to_server(
        WorkforceServicer(Employees(db, source)), server
    )
