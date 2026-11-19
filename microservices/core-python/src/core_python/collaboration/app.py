"""Wiring: CollaborationService on a gRPC server (alone in tests, or core-python's server)."""

from __future__ import annotations

import grpc

from wfk.collaboration_service.v1 import collaboration_pb2_grpc
from wfk_platform import GrpcHealth, HealthRegistry, add_health
from wfk_platform.db import Database

from core_python.collaboration.adapters.answerer import Answerer, gateway_provider
from core_python.collaboration.adapters.colleagues import Colleagues, WorkforceColleagues
from core_python.collaboration.adapters.postgres.conversations import Conversations
from core_python.collaboration.adapters.postgres.standups import Standups
from core_python.collaboration.api.collaboration import CollaborationServicer
from core_python.collaboration.settings import Settings

# This module's event stream: it owns every collab.* subject.
STREAM = "COLLAB"
SUBJECTS = ["collab.>"]


def source(settings: Settings) -> str:
    return f"//{settings.cell_id}/{settings.service_name}"


def create_server(
    settings: Settings,
    db: Database,
    *,
    port: int | None = None,
    answerer: Answerer | None = None,
    colleagues: Colleagues | None = None,
) -> tuple[grpc.aio.Server, GrpcHealth, int]:
    """This module alone on a gRPC server, with health (tests)."""
    registry = HealthRegistry(timeout_s=settings.readiness_timeout_s)
    registry.add("postgres", db.ping)
    server = grpc.aio.server()
    health = add_health(server, registry)
    add_services(server, settings, db, answerer=answerer, colleagues=colleagues)
    bound = server.add_insecure_port(f"{settings.host}:{settings.port if port is None else port}")
    return server, health, bound


def add_services(
    server: grpc.aio.Server,
    settings: Settings,
    db: Database,
    *,
    answerer: Answerer | None = None,
    colleagues: Colleagues | None = None,
) -> None:
    """CollaborationService, on a server that may also carry other modules' services."""
    answerer = answerer or Answerer(
        gateway_provider(settings.model_gateway_url, settings.model_gateway_key)
    )
    colleagues = colleagues or WorkforceColleagues(settings.workforce_address)
    conversations = Conversations(db, source(settings))
    standups = Standups(db, source(settings), conversations)
    servicer = CollaborationServicer(conversations, answerer, colleagues, standups)
    collaboration_pb2_grpc.add_CollaborationServiceServicer_to_server(servicer, server)
