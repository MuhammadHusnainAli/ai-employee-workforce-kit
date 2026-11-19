"""Ledger writes as activities, so the workflow records every transition durably."""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from core_python.agent_runtime.adapters.ledger import Ledger
from core_python.agent_runtime.workflows.types import LedgerEntry, StartRecord


@dataclass
class LedgerActivities:
    ledger: Ledger

    @activity.defn(name="record_start")
    async def record_start(self, rec: StartRecord) -> bool:
        return await self.ledger.start(rec)

    @activity.defn(name="record_step")
    async def record_step(self, entry: LedgerEntry) -> bool:
        return await self.ledger.record(entry)

    def all(self) -> list[object]:
        return [self.record_start, self.record_step]
