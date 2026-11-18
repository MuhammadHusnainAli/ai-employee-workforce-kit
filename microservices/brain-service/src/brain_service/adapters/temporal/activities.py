"""BrainIngest's activities, bound to their adapters by the ingest process."""

from __future__ import annotations

import uuid
from typing import Any

from temporalio import activity

from brain_service.adapters.modelgw.extractor import Extractor
from brain_service.adapters.postgres.consolidate import Batch, Consolidation
from brain_service.adapters.postgres.learn import Learning
from brain_service.adapters.postgres.skills import Skills
from brain_service.adapters.temporal.consolidate import Employee
from brain_service.adapters.temporal.ingest import RememberInput
from brain_service.domain.learn import consolidate, skills
from brain_service.domain.write.model import Candidate, Committed, Source
from brain_service.remember import Remember


class IngestActivities:
    def __init__(self, extractor: Extractor, remember: Remember, learning: Learning) -> None:
        self.extractor = extractor
        self.remember_ = remember
        self.learning = learning

    @activity.defn(name="brain_credit")
    async def credit(self, source: Source) -> int:
        if not source.run_id:
            return 0
        return await self.learning.credit_run(
            source.tenant_id, source.employee_id, source.run_id, source.outcome
        )

    @activity.defn(name="brain_extract")
    async def extract(self, source: Source) -> list[Candidate]:
        return await self.extractor.extract(source)

    @activity.defn(name="brain_remember")
    async def remember(self, inp: RememberInput) -> Committed:
        return await self.remember_(inp.source, inp.candidates)


class ConsolidateActivities:
    """Consolidation's steps (adapters/temporal/consolidate.py), bound to brain_db."""

    def __init__(
        self, consolidation: Consolidation, remember: Remember, skills_: Skills | None = None
    ) -> None:
        self.c = consolidation
        self.remember_ = remember
        self.skills = skills_ or Skills(consolidation.db, service_source=remember.service_source)

    @activity.defn(name="consolidate_due")
    async def due(self) -> list[Employee]:
        return [Employee(tenant_id=t, employee_id=e) for t, e in await self.c.due()]

    @activity.defn(name="consolidate_select")
    async def select(self, who: Employee) -> Batch:
        return await self.c.select(who.tenant_id, who.employee_id)

    @activity.defn(name="consolidate_remember")
    async def remember(self, batch: Batch) -> int:
        """What this batch's clusters teach, through the governed write path. Idempotent: the
        source id is derived from the batch, so a retried step stores nothing twice."""
        candidates = consolidate.distil(Consolidation.episodes(batch))
        if not candidates or batch.until is None:
            return 0
        source = Source(
            event_id=uuid.uuid5(uuid.UUID(batch.tenant_id),
                                f"consolidate/{batch.employee_id}/{batch.until.isoformat()}"),
            event_type="brain.consolidation",
            tenant_id=uuid.UUID(batch.tenant_id),
            employee_id=batch.employee_id,
            task="Consolidation of recent experiences",
            outcome="unknown",
        )  # fmt: skip
        done = await self.remember_(source, candidates)
        return len(done.memory_ids)

    @activity.defn(name="consolidate_skills")
    async def mine_skills(self, who: Employee) -> int:
        """Propose a skill for each tool sequence that keeps succeeding. Idempotent: a tool
        sequence that already has a skill is skipped, and ids derive from the sequence."""
        found = skills.mine(
            await self.skills.successes(who.tenant_id, who.employee_id),
            await self.skills.known(who.tenant_id, who.employee_id),
        )
        if not found:
            return 0
        vectors = await self.remember_.embedder.embed([m.trigger for m in found])
        proposed = 0
        for mined, vec in zip(found, vectors, strict=True):
            if await self.skills.propose(who.tenant_id, who.employee_id, mined, vec):
                proposed += 1
        return proposed

    @activity.defn(name="consolidate_decay")
    async def decay(self, who: Employee) -> dict[str, int]:
        return await self.c.decay(who.tenant_id, who.employee_id)

    @activity.defn(name="consolidate_merge")
    async def merge(self, who: Employee) -> int:
        return await self.c.merge_entities(who.tenant_id, who.employee_id)

    @activity.defn(name="consolidate_finish")
    async def finish(self, args: dict[str, Any]) -> None:
        batch = Batch.model_validate(args["batch"])
        await self.c.finish(batch, dict(args["counts"]))
