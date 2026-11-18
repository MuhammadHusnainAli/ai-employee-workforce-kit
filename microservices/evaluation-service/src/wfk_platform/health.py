"""Liveness and readiness over the standard gRPC health protocol (grpc.health.v1).

A Python service listens on one port, gRPC only. Kubernetes probes it with native gRPC probes:

    liveness   service ""           SERVING while the process serves requests. Never checks
                                    dependencies, so a database outage does not restart pods.
    readiness  service "readiness"  runs the registered checks, each with a deadline;
                                    NOT_SERVING if any fails.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

log = logging.getLogger(__name__)

Check = Callable[[], Awaitable[None]]
"""A readiness check. Returns normally when healthy, raises when not."""

LIVENESS = ""
READINESS = "readiness"
SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING


class HealthRegistry:
    def __init__(self, timeout_s: float = 2.0) -> None:
        self.timeout_s = timeout_s
        self._checks: dict[str, Check] = {}

    def add(self, name: str, check: Check) -> None:
        self._checks[name] = check

    async def _run(self, name: str, check: Check) -> tuple[str, str]:
        try:
            await asyncio.wait_for(check(), timeout=self.timeout_s)
        except TimeoutError:
            return name, f"timeout after {self.timeout_s}s"
        except Exception as exc:  # any failure means not ready
            return name, f"error: {type(exc).__name__}"
        return name, "ok"

    async def readiness(self) -> tuple[bool, dict[str, str]]:
        results = await asyncio.gather(*(self._run(n, c) for n, c in self._checks.items()))
        status = dict(results)
        ready = all(v == "ok" for v in status.values())
        if not ready:
            log.warning("not ready", extra={"checks": status})
        return ready, status


class GrpcHealth(health_pb2_grpc.HealthServicer):
    """grpc.health.v1.Health: liveness on service "", readiness on service "readiness"."""

    def __init__(self, registry: HealthRegistry) -> None:
        self.registry = registry
        self.serving = True

    def shutdown(self) -> None:
        """Report NOT_SERVING from now on, so traffic drains before the server stops."""
        self.serving = False

    async def status(self, service: str) -> int | None:
        if service == LIVENESS:
            return SERVING if self.serving else NOT_SERVING
        if service == READINESS:
            ready, _ = await self.registry.readiness()
            return SERVING if ready and self.serving else NOT_SERVING
        return None

    async def Check(
        self, request: health_pb2.HealthCheckRequest, context: grpc.aio.ServicerContext
    ) -> health_pb2.HealthCheckResponse:
        status = await self.status(request.service)
        if status is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"unknown service {request.service!r}")
        return health_pb2.HealthCheckResponse(status=status)

    async def Watch(
        self, request: health_pb2.HealthCheckRequest, context: grpc.aio.ServicerContext
    ) -> None:
        await context.abort(grpc.StatusCode.UNIMPLEMENTED, "use Check")


def add_health(server: grpc.aio.Server, registry: HealthRegistry) -> GrpcHealth:
    health = GrpcHealth(registry)
    health_pb2_grpc.add_HealthServicer_to_server(health, server)
    return health
