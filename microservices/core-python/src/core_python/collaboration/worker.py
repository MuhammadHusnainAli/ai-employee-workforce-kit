"""The collaboration module's background part: the Standup worker (queue `collab`) and the
inbox that turns its own events into workflow starts and signals."""

from __future__ import annotations

import logging
from typing import Any

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

from wfk_platform.db import Conn
from wfk_platform.outbox import CloudEvent

from core_python.collaboration.standup.activities import StandupActivities
from core_python.collaboration.standup.types import TASK_QUEUE, StandupInput
from core_python.collaboration.standup.workflow import Standup

log = logging.getLogger(__name__)

CONSUMER = "collaboration-standups"


def build_worker(client: Client, activities: StandupActivities, queue: str = TASK_QUEUE) -> Worker:
    return Worker(client, task_queue=queue, workflows=[Standup], activities=activities.all())


def workflow_id(standup_id: str) -> str:
    return f"standup-{standup_id}"


class StandupEvents:
    """collab.* events, handled once through the inbox; every action is safe to repeat.

    collab.standup_started         start the Standup (REJECT_DUPLICATE: a standup runs once)
    collab.message_posted          the person spoke in a standup: person_posted
    collab.standup_end_requested   end_discussion
    """

    def __init__(self, client: Client, queue: str = TASK_QUEUE) -> None:
        self.client = client
        self.queue = queue

    async def __call__(self, conn: Conn, event: CloudEvent) -> None:
        data: dict[str, Any] = event.data
        match event.type:
            case "collab.standup_started":
                inp = StandupInput(
                    tenant_id=str(event.tenantid),
                    standup_id=str(data["standup_id"]),
                    department_id=str(data.get("department_id", "")),
                    discussion_s=int(data.get("discussion_s", 0)),
                    assign_next_steps=bool(data.get("assign_next_steps", False)),
                )
                try:
                    await self.client.start_workflow(
                        Standup.run,
                        inp,
                        id=workflow_id(inp.standup_id),
                        task_queue=self.queue,
                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    )
                except WorkflowAlreadyStartedError:
                    log.info("standup already started", extra={"standup_id": inp.standup_id})
            case "collab.message_posted":
                if data.get("kind") == "standup" and data.get("author_kind") == "person":
                    await self._signal(str(data["conversation_id"]), Standup.person_posted)
            case "collab.standup_end_requested":
                await self._signal(str(data["standup_id"]), Standup.end_discussion)
            case _:
                pass

    async def _signal(self, standup_id: str, signal: Any) -> None:
        try:
            await self.client.get_workflow_handle(workflow_id(standup_id)).signal(signal)
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
            log.info("no open standup to signal", extra={"standup_id": standup_id})


async def run_workers(settings: Any, *, stop: Any, db: Any) -> None:
    """The Standup worker and the collab.* inbox, until `stop` is set (core-python part
    `collab-workers`). `settings` is the collaboration Settings; `db` is collab_db."""
    import asyncio

    import grpc
    from temporalio.contrib.pydantic import pydantic_data_converter

    from wfk.task_service.v1 import tasks_pb2_grpc
    from wfk.workforce_service.v1 import workforce_pb2_grpc
    from wfk_platform.messaging import run_inbox_consumer

    from core_python.collaboration.adapters.answerer import gateway_provider
    from core_python.collaboration.adapters.postgres.conversations import Conversations
    from core_python.collaboration.adapters.postgres.standups import Standups
    from core_python.collaboration.app import STREAM, source

    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    conversations = Conversations(db, source(settings))
    activities = StandupActivities(
        Standups(db, source(settings), conversations),
        workforce_pb2_grpc.WorkforceServiceStub(
            grpc.aio.insecure_channel(settings.workforce_address)
        ),
        tasks_pb2_grpc.TaskServiceStub(grpc.aio.insecure_channel(settings.task_address)),
        gateway_provider(settings.model_gateway_url, settings.model_gateway_key),
    )
    inbox = asyncio.create_task(
        run_inbox_consumer(
            db,
            settings.nats_url,
            stream=STREAM,
            consumer=CONSUMER,
            subject="collab.>",
            handler=StandupEvents(client),
            stop=stop,
        )
    )
    try:
        async with build_worker(client, activities):
            log.info("standup worker started")
            await stop.wait()
    finally:
        stop.set()
        await asyncio.gather(inbox, return_exceptions=True)
