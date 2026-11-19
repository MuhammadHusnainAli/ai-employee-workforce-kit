"""The tools that make an employee part of the organisation.

- list_team: where you sit in the org chart, and who everyone is
- delegate_task: hand a piece of work to someone below you (a sub-task, its own run)
- wait_for_reports: wait for everything you handed down, then get the results (the EmployeeRun
  workflow does the waiting; this body only answers when there is nothing to wait for)
- send_message / read_messages: direct messages, to colleagues or the person
- ask_colleague: a short call: ask, get an answer, ask again with the same call_id
- request_hire: ask the person to approve a hire (a hire costs money; only the person hires)

Every tool speaks to its module as the calling employee and passes the call's idempotency key,
so a retried step never hands work down twice or sends a message twice. A refusal comes back to
the model as an error it can read and act on; the run never fails because of one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import grpc
from agent_harness.tools import Tool, ToolContext

from wfk.collaboration_service.v1 import collaboration_pb2 as collab
from wfk.common.v1 import common_pb2
from wfk.task_service.v1 import tasks_pb2 as tasks
from wfk.workforce_service.v1 import workforce_pb2 as wf

from core_python.agent_runtime.adapters.org_clients import OrgClients

#: Sub-tasks one step may create (architecture/runtime-flow.md: fan-out at most 10 per step).
MAX_DELEGATIONS_PER_STEP = 10
WAIT_FOR_REPORTS = "wait_for_reports"


class OrgToolError(Exception):
    """A refusal or failure the employee should read and act on."""


def _who(ctx: ToolContext) -> tuple[str, str, str]:
    """(tenant, employee, idempotency key) of the calling run."""
    s = ctx.state
    return (
        str(s.get("tenant_id", "")),
        str(s.get("employee_id", "")),
        str(s.get("idempotency_key", "")),
    )


def _live(ctx: ToolContext, what: str) -> None:
    """Evaluation and shadow runs practise: they never change anything in the company."""
    mode = str(ctx.state.get("mode", "live"))
    if mode != "live":
        raise OrgToolError(f"this is a practice ({mode}) run, so nothing was {what}")


def _tenant(tenant_id: str, employee_id: str) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(
        tenant_id=tenant_id,
        employee_id=employee_id,
        actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_EMPLOYEE, id=employee_id),
    )


def _key(value: str) -> common_pb2.IdempotencyKey:
    return common_pb2.IdempotencyKey(value=value)


async def _call(what: str, rpc: Any, request: Any, deadline_s: float) -> Any:
    try:
        return await rpc(request, timeout=deadline_s)
    except grpc.aio.AioRpcError as exc:
        raise OrgToolError(f"could not {what}: {exc.details() or exc.code().name}") from None


def _step_index(key: str) -> int:
    try:
        return int(key.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return 0


def _describe(e: wf.Employee) -> str:
    title = f" ({e.title})" if e.title else ""
    return f"{e.employee_id}{title}"


def org_tools(clients: OrgClients) -> dict[str, Callable[[], Tool]]:
    """name -> factory, like the in-process catalog."""
    c = clients

    async def list_team(ctx: ToolContext) -> str:
        """Where you sit in the org chart, who reports to you, and everyone in the company."""
        tenant, me, _ = _who(ctx)
        chart = await _call(
            "read the org chart",
            c.workforce.GetOrgChart,
            wf.GetOrgChartRequest(tenant=_tenant(tenant, me)),
            c.deadline_s,
        )
        people = {e.employee_id: e for e in chart.employees}
        depts = {d.department_id: d.name for d in chart.departments}
        lines: list[str] = []
        mine = people.get(me)
        if mine is not None:
            boss = mine.manager_id or "the person (the CEO)"
            dept = (
                f", {depts.get(mine.department_id, mine.department_id)}"
                if mine.department_id
                else ""
            )
            lines.append(f"You: {_describe(mine)}{dept}. Reports to: {boss}.")
            reports = [_describe(e) for e in chart.employees if e.manager_id == me]
            lines.append(
                "Your direct reports: " + (", ".join(reports) if reports else "none") + "."
            )
        lines.append("Everyone:")
        for e in chart.employees:
            role = "co-CEO" if e.is_co_ceo else depts.get(e.department_id, e.department_id or "-")
            boss = e.manager_id or "the person"
            paused = " [paused]" if e.status == wf.EMPLOYEE_STATUS_PAUSED else ""
            lines.append(f"- {_describe(e)}, {role}, reports to {boss}{paused}")
        return "\n".join(lines)

    async def delegate_task(
        employee_id: str, title: str, instructions: str, ctx: ToolContext
    ) -> str:
        """Hand a piece of your task to someone below you in the org chart. It runs as their own
        task; call wait_for_reports to get the results back.

        Args:
            employee_id: who does it (someone below you; the co-CEO may pick anyone).
            title: a short name for the piece of work.
            instructions: everything they need to do it well, including what to hand back.
        """
        _live(ctx, "handed down")
        tenant, me, key = _who(ctx)
        if _step_index(key) >= MAX_DELEGATIONS_PER_STEP:
            raise OrgToolError(
                f"at most {MAX_DELEGATIONS_PER_STEP} hand-offs per step; wait for these first"
            )
        check = await _call(
            "check the org chart",
            c.workforce.CanDelegate,
            wf.CanDelegateRequest(
                tenant=_tenant(tenant, me), from_employee=me, to_employee=employee_id
            ),
            c.deadline_s,
        )
        if not check.allowed:
            raise OrgToolError(f"cannot hand this to {employee_id}: {check.reason}")
        body = (
            f"{instructions.strip()}\n\nHanded down by {me} as part of a larger goal. When you "
            f"are done, give a clear final answer: it goes back to {me}."
        )
        got = await _call(
            "hand the task down",
            c.tasks.CreateTask,
            tasks.CreateTaskRequest(
                tenant=_tenant(tenant, me),
                idempotency_key=_key(key),
                employee_id=employee_id,
                title=title.strip()[:200],
                input=body,
                parent_task_id=ctx.run_id,
            ),
            c.deadline_s,
        )
        return (
            f"Handed to {employee_id} as task {got.task.task_id}. Call wait_for_reports "
            "when you need the results."
        )

    async def wait_for_reports() -> str:
        """Wait until every task you handed down has finished, then get all their results."""
        return "You have no handed-down tasks to wait for."

    async def send_message(to: str, text: str, ctx: ToolContext) -> str:
        """Send a direct message to a colleague, or to the person who runs the company.

        Args:
            to: an employee id, or "person" for the person (the CEO).
            text: the message.
        """
        _live(ctx, "sent")
        tenant, me, key = _who(ctx)
        await _call(
            "send the message",
            c.collaboration.SendMessage,
            collab.SendMessageRequest(
                tenant=_tenant(tenant, me), idempotency_key=_key(key), to=to, text=text
            ),
            c.deadline_s,
        )
        return f"Sent to {to}."

    async def read_messages(ctx: ToolContext) -> str:
        """Read your unread direct messages. Each is shown once."""
        tenant, me, _ = _who(ctx)
        got = await _call(
            "read your messages",
            c.collaboration.ReadInbox,
            collab.ReadInboxRequest(tenant=_tenant(tenant, me)),
            c.deadline_s,
        )
        if not got.messages:
            return "No unread messages."
        return "\n".join(f"From {m.author}: {m.text}" for m in got.messages)

    async def ask_colleague(
        employee_id: str, question: str, ctx: ToolContext, call_id: str = ""
    ) -> str:
        """Ask a colleague a question on a short call and get their answer. To follow up, ask
        again with the same call_id. A call ends after 6 questions.

        Args:
            employee_id: who to ask.
            question: what you want to know.
            call_id: the call to continue; leave empty to start a new call.
        """
        _live(ctx, "asked")
        tenant, me, key = _who(ctx)
        got = await _call(
            f"reach {employee_id}",
            c.collaboration.AskColleague,
            collab.AskColleagueRequest(
                tenant=_tenant(tenant, me),
                idempotency_key=_key(key),
                colleague=employee_id,
                question=question,
                conversation_id=call_id,
            ),
            c.call_deadline_s,
        )
        return (
            f"{employee_id}: {got.answer}\n\n(call_id={got.conversation_id}; "
            f"{got.turns_left} questions left on this call)"
        )

    async def request_hire(
        employee_id: str,
        name: str,
        title: str,
        role: str,
        reason: str,
        ctx: ToolContext,
        department_id: str = "",
        manager_id: str = "",
        instructions: str = "",
        model: str = "",
    ) -> str:
        """Ask the person (the CEO) to approve hiring a new employee. A hire costs money, so
        nothing is hired until the person approves.

        Args:
            employee_id: the new employee's id: lowercase letters, digits and dashes.
            name: the new employee's name.
            title: their job title, such as "Sales Intern".
            role: what they do, in a sentence or two.
            reason: why the company needs them now.
            department_id: the department they join, if any.
            manager_id: who they report to; empty means the person.
            instructions: how they should work.
            model: the model they think with; empty means the same as yours.
        """
        _live(ctx, "requested")
        tenant, me, key = _who(ctx)
        if not model:
            mine = await _call(
                "look up your model",
                c.workforce.GetVersion,
                wf.GetVersionRequest(tenant=_tenant(tenant, me), employee_id=me),
                c.deadline_s,
            )
            model = mine.version.model
        hire = wf.Hire(
            employee_id=employee_id,
            name=name,
            title=title,
            role=role,
            department_id=department_id,
            manager_id=manager_id,
            instructions=instructions,
            model=model,
        )
        got = await _call(
            "ask for the hire",
            c.workforce.RequestHire,
            wf.RequestHireRequest(
                tenant=_tenant(tenant, me), idempotency_key=_key(key), hire=hire, reason=reason
            ),
            c.deadline_s,
        )
        return (
            f"Hire request {got.request.request_id} is waiting for the person to approve it. "
            "Nothing is hired until they do."
        )

    fns: list[Callable[..., Any]] = [
        list_team,
        delegate_task,
        wait_for_reports,
        send_message,
        read_messages,
        ask_colleague,
        request_hire,
    ]
    return {fn.__name__: (lambda fn=fn: Tool(fn)) for fn in fns}
