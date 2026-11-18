"""Nightly consolidation as Temporal workflows, on task queue `brain-batch`.

    ConsolidateAll (on a schedule)  ->  BrainConsolidate per employee with new episodes:
        select  ->  remember what the clusters teach  ->  decay  ->  merge entities  ->  finish

Resumable: each step is an activity, retried on failure and never repeated once done. Rate
limited: the brain-batch worker runs few activities at once, and at most MAX_PARALLEL
employees at a time, so a night's backlog never crowds out recall.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel

    from brain_service.adapters.postgres.consolidate import Batch

TASK_QUEUE = "brain-batch"
SCHEDULE_ID = "brain-consolidate-nightly"
MAX_PARALLEL = 4
RETRY = RetryPolicy(maximum_interval=timedelta(minutes=1))
STEP = timedelta(minutes=5)


class Employee(BaseModel):
    tenant_id: str
    employee_id: str


class Outcome(BaseModel):
    employee_id: str
    counts: dict[str, int]


@workflow.defn(name="BrainConsolidate")
class BrainConsolidate:
    @workflow.run
    async def run(self, who: Employee) -> Outcome:
        opts = {"start_to_close_timeout": STEP, "retry_policy": RETRY}
        batch: Batch = await workflow.execute_activity(
            "consolidate_select", who, result_type=Batch, **opts
        )
        learned: int = await workflow.execute_activity(
            "consolidate_remember", batch, result_type=int, **opts
        )
        decay: dict[str, int] = await workflow.execute_activity(
            "consolidate_decay", who, result_type=dict[str, int], **opts
        )
        merged: int = await workflow.execute_activity(
            "consolidate_merge", who, result_type=int, **opts
        )
        counts = {"episodes": len(batch.episodes), "learned": learned, "merged_entities": merged,
                  **decay}  # fmt: skip
        await workflow.execute_activity(
            "consolidate_finish", {"batch": batch, "counts": counts}, **opts
        )
        return Outcome(employee_id=who.employee_id, counts=counts)


@workflow.defn(name="ConsolidateAll")
class ConsolidateAll:
    """Every employee with new episodes, a few at a time."""

    @workflow.run
    async def run(self) -> int:
        due: list[Employee] = await workflow.execute_activity(
            "consolidate_due", result_type=list[Employee],
            start_to_close_timeout=STEP, retry_policy=RETRY,
        )  # fmt: skip
        run = workflow.info().run_id[:8]  # unique per pass, so a second pass the same day works
        for i in range(0, len(due), MAX_PARALLEL):
            await asyncio.gather(*(
                workflow.execute_child_workflow(
                    BrainConsolidate.run, e,
                    # The parent's own queue, wherever it runs.
                    id=f"brain-consolidate-{e.tenant_id}-{e.employee_id}-{run}",
                )
                for e in due[i : i + MAX_PARALLEL]
            ))  # fmt: skip
        return len(due)
