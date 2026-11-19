"""brain-ingest: the write path (brain.md section 8), part `brain-ingest` of core-python.

One process, three loops:

- a Temporal worker on queue `brain-ingest` running `BrainIngest`;
- an inbox consumer on agent-runtime's `RUNS` stream: each finished run starts one
  `BrainIngest` (workflow id from the event id, so a redelivered event starts nothing new);
- the outbox relay publishing `brain.memory_committed` to the `BRAIN` stream.

It scales on the ingest backlog, apart from `brain-api`, so remembering never slows recall.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from wfk_platform.db import Conn, Database
from wfk_platform.messaging import run_inbox_consumer, run_outbox_relay
from wfk_platform.outbox import CloudEvent

from core_python.brain.adapters.modelgw.extractor import (
    BaselineExtractor,
    Extractor,
    GatewayExtractor,
    gateway_provider,
)
from core_python.brain.adapters.postgres.consolidate import Consolidation
from core_python.brain.adapters.postgres.learn import Learning
from core_python.brain.adapters.temporal.activities import ConsolidateActivities, IngestActivities
from core_python.brain.adapters.temporal.consolidate import (
    SCHEDULE_ID,
    BrainConsolidate,
    ConsolidateAll,
)
from core_python.brain.adapters.temporal.consolidate import TASK_QUEUE as BATCH_QUEUE
from core_python.brain.adapters.temporal.ingest import TASK_QUEUE, BrainIngest, workflow_id
from core_python.brain.domain.write import sources
from core_python.brain.remember import Remember
from core_python.brain.settings import Settings

log = logging.getLogger(__name__)

STREAM = "BRAIN"
RUNS_STREAM = "RUNS"
RUNS_CONSUMER = "brain-ingest-runs"
EVAL_STREAM = "EVAL"
EVAL_CONSUMER = "brain-evals"


async def on_evaluation(conn: Conn, event: CloudEvent) -> None:
    """evaluation-service's verdict on a proposal: the gate DecideProposal checks."""
    if event.type == "eval.completed":
        from core_python.brain.adapters.postgres.rules import record_evaluation

        await record_evaluation(conn, event.data)


class RunsToIngest:
    """Inbox handler: a finished run becomes one BrainIngest workflow."""

    def __init__(self, client: Client, task_queue: str = TASK_QUEUE) -> None:
        self.client = client
        self.task_queue = task_queue

    async def __call__(self, conn: Conn, event: CloudEvent) -> None:
        source = sources.from_run_event(
            event.type, event.id, event.tenantid, event.time, event.data
        )
        if source is None:
            return
        try:
            await self.client.start_workflow(
                BrainIngest.run,
                source,
                id=workflow_id(source),
                task_queue=self.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            pass  # redelivered: the run is already being (or was) remembered


def extractor(settings: Settings) -> Extractor:
    if settings.extractor == "gateway":
        provider = gateway_provider(settings.model_gateway_url, settings.model_gateway_key)
        return GatewayExtractor(provider, settings.extraction_model)
    return BaselineExtractor()


async def temporal_client(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )


def batch_worker(
    client: Client, activities: ConsolidateActivities, task_queue: str = BATCH_QUEUE
) -> Worker:
    """Consolidation: few activities at a time, so a night's backlog stays gentle."""
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[BrainConsolidate, ConsolidateAll],
        activities=[activities.due, activities.select, activities.remember,
                    activities.mine_skills, activities.decay, activities.merge, activities.finish],
        max_concurrent_activities=2,
    )  # fmt: skip


async def ensure_schedule(client: Client, cron: str, schedule_id: str = SCHEDULE_ID) -> None:
    """The nightly ConsolidateAll. Created once; later starts (every restart) leave it as is."""
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleAlreadyRunningError,
        ScheduleSpec,
    )

    try:
        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    ConsolidateAll.run, id="brain-consolidate-all", task_queue=BATCH_QUEUE
                ),
                spec=ScheduleSpec(cron_expressions=[cron]),
            ),
        )
        log.info("consolidation scheduled", extra={"cron": cron})
    except ScheduleAlreadyRunningError:
        pass  # created by an earlier start


def worker(client: Client, activities: IngestActivities, task_queue: str = TASK_QUEUE) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[BrainIngest],
        activities=[activities.credit, activities.extract, activities.remember],
    )


async def ingest(settings: Settings, *, stop: asyncio.Event, db: Database | None = None) -> None:
    """The write path until `stop` is set. `db` is brain_db; when given it is shared and stays
    open, otherwise this opens its own."""
    from core_python.brain.app import embedder

    own_db = db is None
    if db is None:
        db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
        await db.open()
    client = await temporal_client(settings)
    remember = Remember(
        db,
        embedder(settings),
        default_pii=settings.pii_policy,
        service_source=f"//{settings.cell_id}/{settings.service_name}",
    )
    loops = [
        asyncio.create_task(
            run_outbox_relay(db, settings.nats_url, stream=STREAM, subjects=["brain.>"], stop=stop)
        ),
        asyncio.create_task(
            run_inbox_consumer(
                db,
                settings.nats_url,
                stream=RUNS_STREAM,
                consumer=RUNS_CONSUMER,
                subject="run.>",
                handler=RunsToIngest(client),
                stop=stop,
            )
        ),
        asyncio.create_task(
            run_inbox_consumer(
                db,
                settings.nats_url,
                stream=EVAL_STREAM,
                consumer=EVAL_CONSUMER,
                subject="eval.completed",
                handler=on_evaluation,
                stop=stop,
            )
        ),
    ]
    try:
        activities = IngestActivities(extractor(settings), remember, Learning(db))
        await ensure_schedule(client, settings.consolidation_cron)
        batch = ConsolidateActivities(Consolidation(db), remember)
        async with worker(client, activities), batch_worker(client, batch):
            log.info("ingest started", extra={"task_queue": TASK_QUEUE})
            await stop.wait()
    finally:
        stop.set()
        await asyncio.gather(*loops, return_exceptions=True)
        if own_db:
            await db.close()
