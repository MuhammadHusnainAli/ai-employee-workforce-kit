"""Entry point: the evaluation worker. No port: evaluations start from events (every
`brain.change_proposed`) and their verdicts go out as events (`eval.completed`).

One process, three loops:
- the Temporal worker on queue `eval` (EvalProposal);
- an inbox consumer on the BRAIN stream, starting one evaluation per proposal;
- the outbox relay publishing eval.* to the EVAL stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from wfk_platform import configure_logging, setup_telemetry
from wfk_platform.db import Database
from wfk_platform.messaging import run_inbox_consumer, run_outbox_relay

from core_python.evaluation.activities import EvalActivities, ProposalsToEval
from core_python.evaluation.adapters.stores import BrainEpisodes, Evals
from core_python.evaluation.settings import Settings
from core_python.evaluation.workflows import TASK_QUEUE, EvalProposal

log = logging.getLogger(__name__)

STREAM = "EVAL"
BRAIN_STREAM = "BRAIN"
BRAIN_CONSUMER = "evaluation-proposals"


async def temporal_client(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )


def worker(client: Client, activities: EvalActivities, task_queue: str = TASK_QUEUE) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[EvalProposal],
        activities=[activities.build_suite, activities.run_suite, activities.finish],
    )


async def work(settings: Settings) -> None:
    """The evaluation worker as its own process, until SIGINT / SIGTERM."""
    configure_logging(settings.service_name, settings.log_level, json_output=settings.log_json)
    telemetry = setup_telemetry(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows: Ctrl-C raises instead
            loop.add_signal_handler(sig, stop.set)
    try:
        await evaluate(settings, stop=stop)
    finally:
        telemetry.shutdown()


async def evaluate(settings: Settings, *, stop: asyncio.Event, db: Database | None = None) -> None:
    """Evaluations until `stop` is set. `db` is eval_db; when given it is shared and stays
    open, otherwise this opens its own."""
    own_db = db is None
    if db is None:
        db = Database(settings.database_url, tenant_role=settings.db_tenant_role)
        await db.open()
    client = await temporal_client(settings)
    activities = EvalActivities(
        client,
        Evals(db, service_source=f"//{settings.cell_id}/{settings.service_name}"),
        BrainEpisodes(settings.brain_address) if settings.brain_address else None,
        suites_dir=Path(settings.suites_dir),
        run_queue=settings.eval_run_queue,
        run_timeout_s=settings.run_timeout_s,
        concurrency=settings.concurrency,
    )
    loops = [
        asyncio.create_task(
            run_outbox_relay(db, settings.nats_url, stream=STREAM, subjects=["eval.>"], stop=stop)
        ),
        asyncio.create_task(
            run_inbox_consumer(
                db,
                settings.nats_url,
                stream=BRAIN_STREAM,
                consumer=BRAIN_CONSUMER,
                subject="brain.change_proposed",
                handler=ProposalsToEval(client),
                stop=stop,
            )
        ),
    ]
    try:
        async with worker(client, activities):
            log.info("evaluation worker started", extra={"task_queue": TASK_QUEUE})
            await stop.wait()
    finally:
        stop.set()
        await asyncio.gather(*loops, return_exceptions=True)
        if own_db:
            await db.close()


def run() -> None:
    settings = Settings()
    # psycopg's async driver needs a selector loop; Windows defaults to Proactor.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(work(settings), loop_factory=factory)


if __name__ == "__main__":
    run()
