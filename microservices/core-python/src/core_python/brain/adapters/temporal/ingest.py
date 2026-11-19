"""BrainIngest: the write path as a Temporal workflow, on task queue `brain-ingest`.

    credit (the memories the run recalled)  ->  extract (model call, retried)
        ->  remember (govern, embed in one batch, resolve, commit)

The activities are in `activities.py`; this module imports only data types, so the workflow
sandbox never loads database or network code.

Durable: a crash between the two resumes after the extraction, which is not paid for twice.
Idempotent: the workflow id is derived from the source event, and the commit is idempotent on
it too. Recall never waits for any of this.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel

    from core_python.brain.domain.write.model import Candidate, Committed, Source

TASK_QUEUE = "brain-ingest"


class RememberInput(BaseModel):
    source: Source
    candidates: list[Candidate]


def workflow_id(source: Source) -> str:
    return f"brain-ingest-{source.event_id}"


@workflow.defn(name="BrainIngest")
class BrainIngest:
    @workflow.run
    async def run(self, source: Source) -> Committed:
        # Credit assignment came later: runs started before it replay without the step.
        if workflow.patched("credit-v1"):
            await workflow.execute_activity(
                "brain_credit",
                source,
                result_type=int,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_interval=timedelta(seconds=30)),
            )
        candidates = await workflow.execute_activity(
            "brain_extract",
            source,
            result_type=list[Candidate],
            start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=5, maximum_interval=timedelta(seconds=30)),
        )
        return await workflow.execute_activity(
            "brain_remember",
            RememberInput(source=source, candidates=candidates),
            result_type=Committed,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_interval=timedelta(seconds=30)),
        )
