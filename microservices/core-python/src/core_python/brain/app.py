"""Wiring: one gRPC server on one port, with health and BrainService (Recall, Inspector)."""

from __future__ import annotations

import grpc

from wfk.brain_service.v1 import brain_pb2_grpc
from wfk_platform import GrpcHealth, HealthRegistry, add_health

from core_python.brain.adapters.postgres.feedback import Feedback
from core_python.brain.adapters.postgres.inspect import Inspector
from core_python.brain.adapters.postgres.rules import Rules
from core_python.brain.api.brain import BrainServicer
from core_python.brain.recall import Recall
from core_python.brain.remember import Remember
from core_python.brain.settings import Settings


def create_server(
    settings: Settings, recall: Recall, *, port: int | None = None
) -> tuple[grpc.aio.Server, GrpcHealth, int]:
    """The service's gRPC server: health (liveness "", readiness "readiness") and BrainService.

    Returns the server, its health servicer and the bound port (`port=0` picks a free one).
    """
    registry = HealthRegistry(timeout_s=settings.readiness_timeout_s)
    registry.add("postgres", recall.db.ping)
    server = grpc.aio.server()
    health = add_health(server, registry)
    add_services(server, settings, recall)
    bound = server.add_insecure_port(f"{settings.host}:{settings.port if port is None else port}")
    return server, health, bound


def add_services(server: grpc.aio.Server, settings: Settings, recall: Recall) -> None:
    """BrainService (Recall, Inspector, feedback, rules), on a server that may also carry other
    modules' services."""
    source = f"//{settings.cell_id}/{settings.service_name}"
    inspector = Inspector(recall.db, recall.embedder, service_source=source)
    remember = Remember(
        recall.db, recall.embedder, default_pii=settings.pii_policy, service_source=source
    )
    feedback = Feedback(recall.db, remember, service_source=source)
    servicer = BrainServicer(recall, inspector, feedback, Rules(recall.db, service_source=source))
    brain_pb2_grpc.add_BrainServiceServicer_to_server(servicer, server)
