"""Run retrievers in parallel, each under its own deadline (brain.md section 3, step 3).

A slow retriever is skipped and flagged; a failing one too. Recall never waits on the
slowest store and never fails because one store is down.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from brain_service.domain.memory.model import Kind, Memory

log = logging.getLogger(__name__)

RETRIEVER_TIMEOUT_S = 0.150


@dataclass(frozen=True)
class RecallQuery:
    tenant_id: str
    employee_id: str
    text: str
    embedding: list[float]
    kinds: tuple[Kind, ...]
    as_of: datetime
    limit: int = 50


Retriever = Callable[[RecallQuery], Awaitable[list[Memory]]]
"""Returns memories best first."""


@dataclass
class RetrieverReport:
    hits: int = 0
    ms: float = 0.0
    skipped: str | None = None  # "timeout" or "error: <type>"


@dataclass
class Retrieval:
    rankings: dict[str, list[Memory]] = field(default_factory=dict)
    reports: dict[str, RetrieverReport] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return any(r.skipped for r in self.reports.values())

    def memories(self) -> dict[str, Memory]:
        """Every distinct memory any retriever returned, by id."""
        return {str(m.id): m for ranking in self.rankings.values() for m in ranking}


async def retrieve(
    query: RecallQuery,
    retrievers: dict[str, Retriever],
    timeout_s: float = RETRIEVER_TIMEOUT_S,
) -> Retrieval:
    async def one(name: str, fn: Retriever) -> tuple[str, list[Memory], RetrieverReport]:
        started = time.perf_counter()
        try:
            hits = await asyncio.wait_for(fn(query), timeout=timeout_s)
        except TimeoutError:
            report = RetrieverReport(skipped="timeout")
            hits = []
        except Exception as exc:  # a store being down must not fail recall
            log.warning("retriever failed", extra={"retriever": name, "error": str(exc)})
            report = RetrieverReport(skipped=f"error: {type(exc).__name__}")
            hits = []
        else:
            report = RetrieverReport(hits=len(hits))
        report.ms = round((time.perf_counter() - started) * 1000, 1)
        return name, hits, report

    results = await asyncio.gather(*(one(n, f) for n, f in retrievers.items()))
    out = Retrieval()
    for name, hits, report in results:
        out.rankings[name] = hits
        out.reports[name] = report
    return out
