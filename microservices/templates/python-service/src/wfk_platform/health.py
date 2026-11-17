"""Liveness (/healthz) and readiness (/readyz) endpoints.

Liveness only says the process is serving requests. It never checks dependencies, so a
database outage does not make Kubernetes restart every pod. Readiness runs the registered
checks, each with a deadline, and returns 503 if any fails.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

Check = Callable[[], Awaitable[None]]
"""A readiness check. Returns normally when healthy, raises when not."""


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
            log.warning("readiness check failed", extra={"check": name, "error": str(exc)})
            return name, f"error: {type(exc).__name__}"
        return name, "ok"

    async def readiness(self) -> tuple[bool, dict[str, str]]:
        results = await asyncio.gather(*(self._run(n, c) for n, c in self._checks.items()))
        status = dict(results)
        return all(v == "ok" for v in status.values()), status


def health_router(registry: HealthRegistry) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        ready, checks = await registry.readiness()
        return JSONResponse(
            {"status": "ok" if ready else "unavailable", "checks": checks},
            status_code=200 if ready else 503,
        )

    return router
