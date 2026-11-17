"""The pinned-context cache in Valkey: each employee's identity core and rules.

Recall writes it through on every call. When brain-api is unreachable, the agent runtime reads
it directly and still has the identity and rules (brain.md section 3, step 6: degrade safely).
The key and value format is part of this service's contract (contracts/README.md), because
another service reads it.

Valkey holds only data that can be rebuilt: every operation here is best effort. A cache that
is down or slow costs the fallback, never a recall.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import valkey.asyncio as valkey
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

TTL_S = 24 * 3600
TIMEOUT_S = 0.05


def key(tenant_id: str, employee_id: str) -> str:
    return f"wfk:brain:pinned:v1:{tenant_id}:{employee_id}"


class PinnedContext(BaseModel):
    """What the agent runtime falls back on: who the employee is and its rules."""

    version: int
    identity: str
    rules: list[str] = Field(default_factory=list)


class PinnedCache(Protocol):
    async def put(self, tenant_id: str, employee_id: str, ctx: PinnedContext | None) -> None: ...
    async def get(self, tenant_id: str, employee_id: str) -> PinnedContext | None: ...


class NoCache:
    async def put(self, tenant_id: str, employee_id: str, ctx: PinnedContext | None) -> None:
        return None

    async def get(self, tenant_id: str, employee_id: str) -> PinnedContext | None:
        return None


class ValkeyPinnedCache:
    def __init__(self, url: str, *, ttl_s: int = TTL_S, timeout_s: float = TIMEOUT_S) -> None:
        self.client = valkey.Valkey.from_url(
            url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s
        )
        self.ttl_s = ttl_s
        self.timeout_s = timeout_s

    async def put(self, tenant_id: str, employee_id: str, ctx: PinnedContext | None) -> None:
        """Write through. No identity yet: remove any stale entry."""
        k = key(tenant_id, employee_id)
        try:
            if ctx is None:
                await asyncio.wait_for(self.client.delete(k), self.timeout_s)
            else:
                await asyncio.wait_for(
                    self.client.set(k, ctx.model_dump_json(), ex=self.ttl_s), self.timeout_s
                )
        except Exception as exc:  # best effort: the cache never fails a recall
            log.warning("pinned cache write failed", extra={"error": str(exc)})

    async def get(self, tenant_id: str, employee_id: str) -> PinnedContext | None:
        try:
            raw = await asyncio.wait_for(
                self.client.get(key(tenant_id, employee_id)), self.timeout_s
            )
        except Exception as exc:
            log.warning("pinned cache read failed", extra={"error": str(exc)})
            return None
        return PinnedContext.model_validate_json(raw) if raw else None

    async def aclose(self) -> None:
        await self.client.aclose()
