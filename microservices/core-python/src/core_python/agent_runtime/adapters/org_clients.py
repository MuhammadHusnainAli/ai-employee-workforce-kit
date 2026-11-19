"""Clients for the modules the org tools call: workforce (the chart, hiring), task (sub-tasks,
in core-rust) and collaboration (messages, calls). Each is the module's gRPC stub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import grpc

from wfk.collaboration_service.v1 import collaboration_pb2_grpc
from wfk.task_service.v1 import tasks_pb2_grpc
from wfk.workforce_service.v1 import workforce_pb2_grpc


@dataclass
class OrgClients:
    workforce: Any  # WorkforceServiceStub
    tasks: Any  # TaskServiceStub
    collaboration: Any  # CollaborationServiceStub
    #: Deadline for ordinary calls; a colleague's answer on a call takes a model call.
    deadline_s: float = 10.0
    call_deadline_s: float = 90.0

    @classmethod
    def connect(cls, workforce: str, tasks: str, collaboration: str) -> OrgClients:
        return cls(
            workforce=workforce_pb2_grpc.WorkforceServiceStub(grpc.aio.insecure_channel(workforce)),
            tasks=tasks_pb2_grpc.TaskServiceStub(grpc.aio.insecure_channel(tasks)),
            collaboration=collaboration_pb2_grpc.CollaborationServiceStub(
                grpc.aio.insecure_channel(collaboration)
            ),
        )
