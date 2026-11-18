"""BrainIngest's activities, bound to their adapters by the ingest process."""

from __future__ import annotations

from temporalio import activity

from brain_service.adapters.modelgw.extractor import Extractor
from brain_service.adapters.postgres.learn import Learning
from brain_service.adapters.temporal.ingest import RememberInput
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
