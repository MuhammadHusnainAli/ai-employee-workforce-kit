"""Wiring: one gRPC server on one port with health, WorkforceService, RunService,
BrainService and CollaborationService. Clients keep the same services and messages; only the
address is shared."""

from __future__ import annotations

from dataclasses import dataclass

import grpc

from wfk_platform import GrpcHealth, HealthRegistry, add_health
from wfk_platform.db import Database

from core_python import settings as config
from core_python.agent_runtime import app as runtime_app
from core_python.brain import app as brain_app
from core_python.brain.app import embedder, pinned_cache
from core_python.brain.recall import Recall
from core_python.collaboration import app as collab_app
from core_python.workforce import app as workforce_app


@dataclass
class Databases:
    """One pool per module database: each module still owns its data (ADR-0017)."""

    workforce: Database
    runtime: Database
    brain: Database
    eval: Database
    collab: Database

    @classmethod
    def from_settings(cls, s: config.Settings) -> Databases:
        role = s.db_tenant_role
        return cls(
            workforce=Database(s.workforce_database_url, tenant_role=role),
            runtime=Database(s.runtime_database_url, tenant_role=role),
            brain=Database(s.brain_database_url, tenant_role=role),
            eval=Database(s.eval_database_url, tenant_role=role),
            collab=Database(s.collab_database_url, tenant_role=role),
        )

    def all(self) -> dict[str, Database]:
        return {
            "workforce": self.workforce,
            "runtime": self.runtime,
            "brain": self.brain,
            "eval": self.eval,
            "collab": self.collab,
        }

    async def open(self, *, wait: bool = True) -> None:
        for db in self.all().values():
            await db.open(wait=wait)

    async def close(self) -> None:
        for db in self.all().values():
            await db.close()


def create_server(
    s: config.Settings, dbs: Databases, *, port: int | None = None
) -> tuple[grpc.aio.Server, GrpcHealth, int]:
    """The service's gRPC server: health (liveness "", readiness "readiness") and every
    module's API. Readiness checks each module's database.

    Returns the server, its health servicer and the bound port (`port=0` picks a free one).
    """
    registry = HealthRegistry(timeout_s=s.readiness_timeout_s)
    for name, db in dbs.all().items():
        registry.add(f"postgres:{name}", db.ping)
    server = grpc.aio.server()
    health = add_health(server, registry)

    workforce_app.add_services(server, config.workforce(s), dbs.workforce)
    runtime_app.add_services(server, dbs.runtime)
    brain_settings = config.brain(s)
    recall = Recall(dbs.brain, embedder(brain_settings), pinned_cache(brain_settings))
    brain_app.add_services(server, brain_settings, recall)
    collab_app.add_services(server, config.collaboration(s), dbs.collab)

    bound = server.add_insecure_port(f"{s.host}:{s.port if port is None else port}")
    return server, health, bound
