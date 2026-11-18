"""brain-ingest: the write path's deployable (brain.md section 8). `brain-service ingest`.

One process, three loops:

- a Temporal worker on queue `brain-ingest` running `BrainIngest`;
- an inbox consumer on agent-runtime's `RUNS` stream: each finished run starts one
  `BrainIngest` (workflow id from the event id, so a redelivered event starts nothing new);
- the outbox relay publishing `brain.memory_committed` to the `BRAIN` stream.

It scales on the ingest backlog, apart from `brain-api`, so remembering never slows recall.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from wfk_platform import configure_logging, setup_telemetry
from wfk_platform.db import Conn, Database
from wfk_platform.messaging import run_inbox_consumer, run_outbox_relay
from wfk_platform.outbox import CloudEvent

from brain_service.adapters.modelgw.extractor import (
    BaselineExtractor,
    Extractor,
    GatewayExtractor,
    gateway_provider,
)
from brain_service.adapters.postgres.consolidate import Consolidation
from brain_service.adapters.postgres.learn import Learning
from brain_service.adapters.temporal.activities import ConsolidateActivities, IngestActivities
from brain_service.adapters.temporal.consolidate import (
    SCHEDULE_ID,
    BrainConsolidate,
    ConsolidateAll,
)
from brain_service.adapters.temporal.consolidate import TASK_QUEUE as BATCH_QUEUE
from brain_service.adapters.temporal.ingest import TASK_QUEUE, BrainIngest, workflow_id
from brain_service.domain.write import sources
from brain_service.remember import Remember
from brain_service.settings import Settings

log = logging.getLogger(__name__)

STREAM = "BRAIN"
RUNS_STREAM = "RUNS"
RUNS_CONSUMER = "brain-ingest-runs"
EVAL_STREAM = "EVAL"
EVAL_CONSUMER = "brain-evals"


async def on_evaluation(conn: Conn, event: CloudEvent) -> None:
    """evaluation-service's verdict on a proposal: the gate DecideProposal checks."""
    if event.type == "eval.completed":
        from brain_service.adapters.postgres.rules import record_evaluation

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
        activities=[activities.due, activities.select, activities.remember, activities.decay,
                    activities.merge, activities.finish],
        max_concurrent_activities=2,
    )  # fmt: skip


async def ensure_schedule(client: Client, cron: str) -> None:
    """The nightly ConsolidateAll. Created once; later starts leave it as it is."""
    from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleSpec
    from temporalio.service import RPCError

    try:
        await client.create_schedule(
            SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    ConsolidateAll.run, id="brain-consolidate-all", task_queue=BATCH_QUEUE
                ),
                spec=ScheduleSpec(cron_expressions=[cron]),
            ),
        )
        log.info("consolidation scheduled", extra={"cron": cron})
    except RPCError as exc:  # already there
        if "already" not in str(exc).lower():
            raise


def worker(client: Client, activities: IngestActivities, task_queue: str = TASK_QUEUE) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[BrainIngest],
        activities=[activities.credit, activities.extract, activities.remember],
    )


async def run_ingest(settings: Settings) -> None:
    from brain_service.main import embedder

    configure_logging(settings.service_name, settings.log_level, json_output=settings.log_json)
    telemetry = setup_telemetry(settings)
    db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
    await db.open()
    client = await temporal_client(settings)
    remember = Remember(
        db,
        embedder(settings),
        default_pii=settings.pii_policy,
        service_source=f"//{settings.cell_id}/{settings.service_name}",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows: Ctrl-C raises instead
            loop.add_signal_handler(sig, stop.set)
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
        await db.close()
        telemetry.shutdown()
