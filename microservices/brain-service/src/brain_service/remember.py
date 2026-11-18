"""Remember: the write path after extraction (brain.md section 4, steps 3 to 6).

    candidates -> governance filter -> one batch of embeddings -> resolve and commit

Recall never waits for this: it runs in brain-ingest, off the hot path.
"""

from __future__ import annotations

from wfk_platform.db import Database

from brain_service.adapters.modelgw.embeddings import Embedder
from brain_service.adapters.postgres import write
from brain_service.domain.write.governance import govern
from brain_service.domain.write.model import Candidate, Committed, Governed, PiiPolicy, Source


class Remember:
    def __init__(
        self, db: Database, embedder: Embedder, *, default_pii: PiiPolicy, service_source: str
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.default_pii: PiiPolicy = default_pii
        self.service_source = service_source

    async def __call__(self, source: Source, candidates: list[Candidate]) -> Committed:
        policy = await write.pii_policy(self.db, source.tenant_id, self.default_pii)
        governed: list[Governed] = []
        for c in candidates:
            g = govern(c, policy)
            if g is not None:
                governed.append(g)
        embeddings = await self.embedder.embed([g.content for g in governed]) if governed else []
        return await write.commit(
            self.db,
            source,
            governed,
            embeddings,
            service_source=self.service_source,
            dropped=len(candidates) - len(governed),
        )
