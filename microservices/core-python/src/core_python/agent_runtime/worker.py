"""Temporal workers for agent-runtime: one per task queue (runs, llm, tools).

Separate queues are bulkheads: a flood of slow model calls never starves tool calls or
workflow tasks, and each queue scales on its own backlog (KEDA).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import PayloadCodec
from temporalio.worker import Worker

from core_python.agent_runtime.activities.harness import HarnessActivities
from core_python.agent_runtime.activities.ledger import LedgerActivities
from core_python.agent_runtime.workflows.employee_run import EmployeeRun
from core_python.agent_runtime.workflows.types import (
    TASK_QUEUE_LLM,
    TASK_QUEUE_RUNS,
    TASK_QUEUE_TOOLS,
)


async def connect(
    address: str, namespace: str = "default", *, codec: PayloadCodec | None = None
) -> Client:
    """Client with the pydantic converter and, when given, the claim-check codec.

    Workers built from this client inherit its converter, so client and workers always agree.
    """
    converter = pydantic_data_converter
    if codec is not None:
        converter = dataclasses.replace(converter, payload_codec=codec)
    return await Client.connect(address, namespace=namespace, data_converter=converter)


def queues(prefix: str = "") -> tuple[str, str, str]:
    """Queue names. Tests pass a prefix so parallel test runs never share a queue."""
    return (prefix + TASK_QUEUE_RUNS, prefix + TASK_QUEUE_LLM, prefix + TASK_QUEUE_TOOLS)


def build_workers(
    client: Client,
    activities: HarnessActivities,
    ledger: LedgerActivities,
    prefix: str = "",
) -> Sequence[Worker]:
    runs, llm, tools = queues(prefix)
    return [
        Worker(
            client,
            task_queue=runs,
            workflows=[EmployeeRun],
            activities=[
                activities.load_employee,
                activities.build_context,
                activities.decide,
                ledger.record_start,
                ledger.record_step,
            ],
        ),
        Worker(client, task_queue=llm, activities=[activities.call_model]),
        Worker(client, task_queue=tools, activities=[activities.act]),
    ]
