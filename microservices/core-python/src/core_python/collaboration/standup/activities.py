"""The Standup workflow's activities: everything that reads the org chart and the task inbox,
calls a model, or writes collab_db. Each write is idempotent, so a retry repeats nothing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_harness.llm_providers import CompletionRequest, Provider
from agent_harness.types import Message
from temporalio import activity
from temporalio.exceptions import ApplicationError

from wfk.common.v1 import common_pb2
from wfk.task_service.v1 import tasks_pb2 as tasks
from wfk.workforce_service.v1 import workforce_pb2 as wf

from core_python.collaboration.adapters.postgres.standups import Standups
from core_python.collaboration.domain.conversations import PERSON
from core_python.collaboration.domain.standup import (
    Member,
    Report,
    Summary,
    TaskFact,
    facts_from,
    idle_report,
    parse_summary,
    parse_update,
    pick_roster,
    reply_prompt,
    summary_prompt,
    update_prompt,
)
from core_python.collaboration.standup.types import (
    FailInput,
    FinishInput,
    ReplyInput,
    Roster,
    StandupInput,
    StatusInput,
    UpdateInput,
)

#: How far back the first standup of a scope looks.
FIRST_LOOKBACK = timedelta(days=1)
TASK_STATUS = {
    tasks.TASK_STATUS_QUEUED: "queued",
    tasks.TASK_STATUS_RUNNING: "running",
    tasks.TASK_STATUS_WAITING_HUMAN: "waiting_human",
    tasks.TASK_STATUS_COMPLETED: "completed",
    tasks.TASK_STATUS_FAILED: "failed",
    tasks.TASK_STATUS_CANCELLED: "cancelled",
}


def _service(tenant_id: str) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(
        tenant_id=tenant_id,
        actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_SERVICE, id="collaboration"),
    )


def _as(tenant_id: str, employee_id: str) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(
        tenant_id=tenant_id,
        employee_id=employee_id,
        actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_EMPLOYEE, id=employee_id),
    )


class StandupActivities:
    def __init__(
        self,
        store: Standups,
        workforce: Any,
        tasks: Any,
        provider: Provider,
        *,
        deadline_s: float = 10.0,
    ) -> None:
        self.store = store
        self.workforce = workforce
        self.tasks = tasks
        self.provider = provider
        self.deadline_s = deadline_s

    async def _ask(self, member: Member, purpose: str, system: str, user: str, tokens: int) -> str:
        req = CompletionRequest.model_validate(
            {
                "model": member.model,
                "system": system,
                "messages": [Message.user(user)],
                "max_tokens": tokens,
                "temperature": 0.2,
                "metadata": {"purpose": purpose, "employee_id": member.employee_id},
            }
        )
        return (await self.provider.complete(req)).message.text or ""

    @activity.defn(name="standup_roster")
    async def roster(self, inp: StandupInput) -> Roster:
        chart = await self.workforce.GetOrgChart(
            wf.GetOrgChartRequest(tenant=_service(inp.tenant_id)), timeout=self.deadline_s
        )
        people = [
            Member(
                employee_id=e.employee_id,
                name=e.name,
                title=e.title,
                department_id=e.department_id,
                manager_id=e.manager_id,
                is_co_ceo=e.is_co_ceo,
                model="",
            )
            for e in chart.employees
            if e.status == wf.EMPLOYEE_STATUS_ACTIVE
        ]
        ceo, members = pick_roster(people, inp.department_id)
        if ceo is None:
            raise ApplicationError(
                "there is no active co-CEO to run the standup: name one in the org chart",
                type="NoCoCeo",
                non_retryable=True,
            )
        filled: dict[str, Member] = {}
        for m in [ceo, *members]:
            if m.employee_id not in filled:
                got = await self.workforce.GetVersion(
                    wf.GetVersionRequest(tenant=_service(inp.tenant_id), employee_id=m.employee_id),
                    timeout=self.deadline_s,
                )
                filled[m.employee_id] = m.model_copy(
                    update={"model": got.version.model, "role": got.version.role}
                )
        roster = Roster(
            ceo=filled[ceo.employee_id], members=[filled[m.employee_id] for m in members]
        )
        await self.store.set_roster(
            inp.tenant_id,
            inp.standup_id,
            ceo.employee_id,
            [ceo.employee_id] + [m.employee_id for m in members],
        )
        return roster

    @activity.defn(name="standup_since")
    async def since(self, inp: StandupInput) -> datetime:
        last = await self.store.since(inp.tenant_id, inp.standup_id, inp.department_id)
        return last or datetime.now(UTC) - FIRST_LOOKBACK

    @activity.defn(name="standup_update")
    async def update(self, u: UpdateInput) -> Report:
        m, inp = u.member, u.inp
        got = await self.tasks.ListTasks(
            tasks.ListTasksRequest(
                tenant=_service(inp.tenant_id), employee_id=m.employee_id, limit=50
            ),
            timeout=self.deadline_s,
        )
        facts = facts_from(
            [
                TaskFact(
                    title=t.title,
                    status=TASK_STATUS.get(t.status, "queued"),
                    updated_at=t.updated_at.ToDatetime(tzinfo=UTC),
                    output=t.output,
                )
                for t in got.tasks
            ],
            since=u.since,
        )
        if facts.idle:
            report = idle_report(m)
        else:
            system, user = update_prompt(m, facts)
            said = parse_update(await self._ask(m, "standup-update", system, user, 300))
            report = said.model_copy(update={"employee_id": m.employee_id, "title": m.title})
        await self.store.add_report(
            inp.tenant_id, inp.standup_id, report.model_dump(mode="json"), report.text()
        )
        return report

    @activity.defn(name="standup_status")
    async def status(self, s: StatusInput) -> None:
        await self.store.set_status(s.inp.tenant_id, s.inp.standup_id, s.status, s.note)

    @activity.defn(name="standup_reply")
    async def reply(self, r: ReplyInput) -> int:
        """Answer what the person said since `after_seq`. Returns the last sequence seen."""
        said = await self.store.discussion(r.inp.tenant_id, r.inp.standup_id)
        new = [m for m in said if m["seq"] > r.after_seq and m["author_kind"] == "person"]
        last = max([r.after_seq, *(m["seq"] for m in said)])
        if not new:
            return last
        talk = [(m["author"], m["text"]) for m in said]
        system, user = reply_prompt(r.ceo, r.reports, talk, scope=_scope(r.inp))
        text = await self._ask(r.ceo, "standup-reply", system, user, 300)
        key = f"standup:{r.inp.standup_id}:reply:{new[-1]['seq']}"
        await self.store.reply(
            r.inp.tenant_id, r.inp.standup_id, r.ceo.employee_id, text.strip() or "Noted.", key
        )
        return last

    @activity.defn(name="standup_finish")
    async def finish(self, f: FinishInput) -> Summary:
        inp = f.inp
        stored = (await self.store.get(inp.tenant_id, inp.standup_id))["summary"]
        if stored:  # a retry: the summary is chosen, no second model call
            summary = Summary.model_validate(stored)
        else:
            said = await self.store.discussion(inp.tenant_id, inp.standup_id)
            talk = [(m["author"], m["text"]) for m in said]
            system, user = summary_prompt(f.ceo, f.reports, talk, scope=_scope(inp))
            text = await self._ask(f.ceo, "standup-summary", system, user, 800)
            summary = parse_summary(
                text, owners={m.employee_id for m in f.members} | {f.ceo.employee_id}
            )
            # A retry keeps the first attempt's summary, so each step keeps its task key and input.
            kept = await self.store.keep_summary(
                inp.tenant_id, inp.standup_id, summary.model_dump(mode="json")
            )
            summary = Summary.model_validate(kept)
        if inp.assign_next_steps:
            for i, step in enumerate(summary.next_steps):
                if step.owner == PERSON:
                    continue
                got = await self.tasks.CreateTask(
                    tasks.CreateTaskRequest(
                        tenant=_as(inp.tenant_id, f.ceo.employee_id),
                        idempotency_key=common_pb2.IdempotencyKey(
                            value=f"standup:{inp.standup_id}:step:{i}"
                        ),
                        employee_id=step.owner,
                        title=step.action[:200],
                        input=f"{step.action}\n\nA next step from the {_scope(inp)} standup, "
                        f"given to you by {f.ceo.employee_id}.",
                    ),
                    timeout=self.deadline_s,
                )
                step.task_id = got.task.task_id
        await self.store.finish(
            inp.tenant_id,
            inp.standup_id,
            summary.model_dump(mode="json"),
            summary.text(),
            f.ceo.employee_id,
        )
        return summary

    @activity.defn(name="standup_fail")
    async def fail(self, f: FailInput) -> None:
        await self.store.fail(f.inp.tenant_id, f.inp.standup_id, f.error)

    def all(self) -> list[Callable[..., Any]]:
        return [
            self.roster,
            self.since,
            self.update,
            self.status,
            self.reply,
            self.finish,
            self.fail,
        ]


def _scope(inp: StandupInput) -> str:
    return f"{inp.department_id} department" if inp.department_id else "company"
