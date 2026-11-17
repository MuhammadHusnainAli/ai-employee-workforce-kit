"""Brain.Recall (brain.md section 3): plan, retrieve, fuse, pack, cite. The application
service: it wires the pure domain (planner, retrieval runner, brain-core) to the adapters."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import brain_core

from wfk_platform.db import Database

from brain_service.adapters.cache.pinned import NoCache, PinnedCache, PinnedContext
from brain_service.adapters.modelgw.embeddings import Embedder
from brain_service.adapters.postgres import brain as brain_repo
from brain_service.adapters.postgres.memories import MemoryRetrievers
from brain_service.domain.memory.model import Memory
from brain_service.domain.recall.planner import StepType, estimate_tokens, plan
from brain_service.domain.recall.retrieval import RecallQuery, Retriever, retrieve

log = logging.getLogger(__name__)

EMBED_TIMEOUT_S = 0.150


@dataclass
class RecallRequest:
    tenant_id: str
    employee_id: str
    query: str
    step_type: StepType = "acting"
    token_budget: int = 0
    run_id: str | None = None
    step: int | None = None
    as_of: datetime | None = None


@dataclass
class RecalledItem:
    memory: Memory
    score: float


@dataclass
class RecallResult:
    recall_id: str
    identity: str = ""
    identity_version: int = 0
    rules: list[str] = field(default_factory=list)
    items: list[RecalledItem] = field(default_factory=list)
    tokens_used: int = 0
    degraded: bool = False
    skipped: list[str] = field(default_factory=list)


class Recall:
    def __init__(self, db: Database, embedder: Embedder, cache: PinnedCache | None = None) -> None:
        self.db = db
        self.embedder = embedder
        self.cache: PinnedCache = cache or NoCache()
        self.stores = MemoryRetrievers(db)

    async def _embed(self, text: str) -> list[float] | None:
        try:
            (vec,) = await asyncio.wait_for(self.embedder.embed([text]), EMBED_TIMEOUT_S)
        except Exception as exc:  # no embedding: vector search is skipped, full text still runs
            log.warning("query embedding failed", extra={"error": str(exc)})
            return None
        return vec

    async def recall(self, req: RecallRequest) -> RecallResult:
        as_of = req.as_of or datetime.now(UTC)
        p = plan(req.step_type, req.token_budget)

        # Pinned first: identity (and, from Phase 6, rules) always go in, budget or not.
        async with self.db.tenant_tx(req.tenant_id) as conn:
            identity = await brain_repo.active_identity(conn, req.employee_id)
        rules: list[str] = []
        # Write the pinned context through to Valkey: the agent runtime's fallback.
        await self.cache.put(
            req.tenant_id,
            req.employee_id,
            PinnedContext(version=identity.version, identity=identity.text, rules=rules)
            if identity
            else None,
        )
        pinned = (estimate_tokens(identity.text) if identity else 0) + sum(
            estimate_tokens(r) for r in rules
        )
        budget_left = max(p.budget - pinned, 0)

        embedding = await self._embed(req.query)
        retrievers: dict[str, Retriever] = {"fulltext": self.stores.fulltext}
        if embedding is not None:
            retrievers["vector"] = self.stores.vector
        query = RecallQuery(
            tenant_id=req.tenant_id,
            employee_id=req.employee_id,
            text=req.query,
            embedding=embedding or [],
            kinds=p.kinds,
            as_of=as_of,
            limit=p.limit,
        )
        retrieval = await retrieve(query, retrievers)
        reports = {name: asdict(rep) for name, rep in retrieval.reports.items()}
        if embedding is None:
            reports["vector"] = {"hits": 0, "ms": 0.0, "skipped": "error: no embedding"}

        found = retrieval.memories()
        candidates = [
            (
                mid, m.kind, m.importance, m.confidence, m.trust, m.age_days(as_of),
                estimate_tokens(m.content),
            )
            for mid, m in found.items()
        ]  # fmt: skip
        rankings = [[str(m.id) for m in ranking] for ranking in retrieval.rankings.values()]
        chosen, used = brain_core.rank_and_pack(rankings, candidates, budget_left)

        skipped = sorted(name for name, rep in reports.items() if rep["skipped"])
        async with self.db.tenant_tx(req.tenant_id) as conn:
            recall_id = await brain_repo.log_recall(
                conn,
                tenant_id=req.tenant_id,
                employee_id=req.employee_id,
                run_id=req.run_id,
                step=req.step,
                query=req.query,
                items=chosen,
                retrievers=reports,
                degraded=bool(skipped),
            )
        return RecallResult(
            recall_id=str(recall_id),
            identity=identity.text if identity else "",
            identity_version=identity.version if identity else 0,
            rules=rules,
            items=[RecalledItem(found[mid], score) for mid, score in chosen],
            tokens_used=pinned + used,
            degraded=bool(skipped),
            skipped=skipped,
        )
