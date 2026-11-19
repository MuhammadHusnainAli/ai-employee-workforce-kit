"""task.* events from task-service, turned into EmployeeRun starts and signals.

The workflow id is the task id, so every command finds its run without a lookup. Each event is
handled once through the inbox, but a Temporal call can succeed just before the inbox
transaction fails, so every action here is safe to repeat:

- task.created starts the run with REJECT_DUPLICATE: a second start, even after the run has
  finished, is "already started" and ignored. A task never runs twice.
- signals set a value (approve, human_reply, cancel), so a repeated signal changes nothing;
  child_finished is keyed by the sub-task, so a repeated one is reported once.
- a signal for a run that has finished or never started is dropped: there is nobody to tell.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from wfk_platform.db import Conn
from wfk_platform.outbox import CloudEvent

from core_python.agent_runtime.harness.reports import ChildReport
from core_python.agent_runtime.workflows.employee_run import EmployeeRun
from core_python.agent_runtime.workflows.types import ApprovalAnswer, RunInput

log = logging.getLogger(__name__)

STREAM = "TASKS"
CONSUMER = "agent-runtime-tasks"
SUBJECT = "task.>"


class TaskCommands:
    def __init__(self, client: Client, runs_queue: str) -> None:
        self.client = client
        self.runs_queue = runs_queue

    async def __call__(self, conn: Conn, event: CloudEvent) -> None:
        data: dict[str, Any] = event.data
        task_id = str(data.get("task_id") or event.subject or "")
        if not task_id:
            log.warning("task event without a task id", extra={"type": event.type})
            return
        match event.type:
            case "task.created":
                await self.start(task_id, str(event.tenantid), data)
            case "task.cancel_requested":
                await self.signal(task_id, EmployeeRun.cancel, str(data.get("reason", "")))
            case "task.approval_decided":
                answer = ApprovalAnswer(
                    approved=bool(data.get("approved")),
                    by=str(data.get("by", "")),
                    note=str(data.get("note", "")),
                )
                await self.signal(task_id, EmployeeRun.approve, answer)
            case "task.reply_given":
                await self.signal(task_id, EmployeeRun.human_reply, str(data.get("text", "")))
            case "task.child_finished":
                # Addressed to the parent (task_id): one of its sub-tasks has finished.
                report = ChildReport(
                    child_task_id=str(data.get("child_task_id", "")),
                    employee_id=str(data.get("employee_id", "")),
                    title=str(data.get("title", "")),
                    status=str(data.get("status", "")),
                    stop_reason=str(data.get("stop_reason", "")),
                    output=str(data.get("output", "")),
                )
                await self.signal(task_id, EmployeeRun.child_finished, report)
            case _:
                pass  # task.state_changed and future events: not ours to act on

    async def start(self, task_id: str, tenant_id: str, data: dict[str, Any]) -> None:
        inp = RunInput(
            run_id=task_id,
            tenant_id=tenant_id,
            employee_id=str(data["employee_id"]),
            task=str(data["input"]),
        )
        try:
            await self.client.start_workflow(
                EmployeeRun.run,
                inp,
                id=task_id,
                task_queue=self.runs_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            log.info("run already started", extra={"run_id": task_id})

    async def signal(self, task_id: str, signal: Any, arg: Any) -> None:
        handle = self.client.get_workflow_handle(task_id)
        try:
            await handle.signal(signal, arg)
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise  # Temporal is unavailable: the inbox redelivers the event later
            log.info("no open run to signal", extra={"run_id": task_id})
