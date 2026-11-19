"""The Standup workflow on the local stack (just up): Temporal and collab_db are real; the org
chart and the task inbox are faked at their gRPC surface; the model is scripted."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from agent_harness import FakeProvider
from agent_harness.llm_providers import CompletionRequest
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from wfk.task_service.v1 import tasks_pb2 as tasks
from wfk.workforce_service.v1 import workforce_pb2 as wf
from wfk_platform.db import Database

from core_python.collaboration.adapters.postgres.conversations import Conversations, Who
from core_python.collaboration.adapters.postgres.standups import Standups
from core_python.collaboration.standup.activities import StandupActivities
from core_python.collaboration.standup.workflow import Standup, StandupInput
from core_python.collaboration.worker import build_worker

pytestmark = pytest.mark.integration
SOURCE = "//cell-test/collaboration"


@dataclass
class Fake:
    """A gRPC stub: records requests; a reply is a message, a callable of the request, or an
    error to raise."""

    replies: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name in ("replies", "calls"):
            raise AttributeError(name)

        async def method(request: Any, **_: Any) -> Any:
            self.calls.append((name, request))
            reply = self.replies.get(name)
            if isinstance(reply, Exception):
                raise reply
            return reply(request) if callable(reply) else reply

        return method

    def sent(self, name: str) -> list[Any]:
        return [r for n, r in self.calls if n == name]


def employee(
    eid: str, title: str, manager: str = "", dept: str = "", ceo: bool = False, paused: bool = False
) -> wf.Employee:
    status = wf.EMPLOYEE_STATUS_PAUSED if paused else wf.EMPLOYEE_STATUS_ACTIVE
    return wf.Employee(
        employee_id=eid,
        name=eid.title(),
        title=title,
        manager_id=manager,
        department_id=dept,
        is_co_ceo=ceo,
        status=status,
    )


def chart(with_ceo: bool = True) -> wf.GetOrgChartResponse:
    people = [
        employee("sales-head", "Head of Sales", "co-ceo", "sales"),
        employee("sales-intern", "Sales Intern", "sales-head", "sales"),
        employee("marketing", "Head of Marketing", "co-ceo", "marketing"),
        employee("on-leave", "Analyst", "co-ceo", paused=True),
    ]
    if with_ceo:
        people.insert(0, employee("co-ceo", "Co-CEO", ceo=True))
    return wf.GetOrgChartResponse(employees=people)


def ts(when: datetime) -> Timestamp:
    t = Timestamp()
    t.FromDatetime(when)
    return t


def tasks_of(req: tasks.ListTasksRequest) -> tasks.ListTasksResponse:
    now = datetime.now(UTC)
    if req.employee_id == "sales-head":
        return tasks.ListTasksResponse(
            tasks=[
                tasks.Task(
                    title="Call Acme",
                    status=tasks.TASK_STATUS_COMPLETED,
                    output="Demo booked for Tuesday",
                    updated_at=ts(now - timedelta(hours=1)),
                ),
            ]
        )
    return tasks.ListTasksResponse()


SUMMARY = {
    "where_we_are": "Acme demo booked",
    "where_we_go": "Close Acme this month",
    "next_steps": [
        {"owner": "sales-head", "action": "Send the Acme quote"},
        {"owner": "person", "action": "Approve the discount"},
    ],
    "risks": ["Quote delay"],
}


def model() -> FakeProvider:
    def script(req: CompletionRequest) -> str:
        purpose = (req.metadata or {}).get("purpose")
        if purpose == "standup-update":
            return json.dumps(
                {"done": "Booked the Acme demo", "next": "Send quote", "blockers": ""}
            )
        if purpose == "standup-reply":
            return "Noted: Acme comes first."
        return json.dumps(SUMMARY)

    return FakeProvider([script], loop=True)


def purposes(p: FakeProvider) -> list[str]:
    return [(r.metadata or {}).get("purpose", "") for r in p.requests]


@dataclass
class Rig:
    client: Client
    queue: str
    store: Standups
    conversations: Conversations
    provider: FakeProvider
    workforce: Fake
    tasks: Fake


@asynccontextmanager
async def rig(
    db: Database, org: Callable[[], wf.GetOrgChartResponse] = chart
) -> AsyncIterator[Rig]:
    try:
        client = await Client.connect("127.0.0.1:7018", data_converter=pydantic_data_converter)
    except Exception as exc:
        pytest.skip(f"Temporal not running (just up): {exc}")
    workforce = Fake(
        {
            "GetOrgChart": org(),
            "GetVersion": wf.GetVersionResponse(
                version=wf.EmployeeVersion(role="Works here.", model="m")
            ),
        }
    )
    task = Fake(
        {
            "ListTasks": tasks_of,
            "CreateTask": lambda r: tasks.CreateTaskResponse(
                task=tasks.Task(task_id=f"t-{r.employee_id}")
            ),
        }
    )
    provider = model()
    conversations = Conversations(db, SOURCE)
    store = Standups(db, SOURCE, conversations)
    queue = f"collab-test-{uuid.uuid4().hex[:8]}"
    worker = build_worker(client, StandupActivities(store, workforce, task, provider), queue)
    async with worker:
        yield Rig(client, queue, store, conversations, provider, workforce, task)


async def start(r: Rig, *, discussion_s: int = 0, assign: bool = False, dept: str = "") -> str:
    tenant = str(uuid.uuid4())
    row = await r.store.start(
        tenant,
        uuid.uuid4().hex,
        department_id=dept,
        discussion_s=discussion_s,
        assign_next_steps=assign,
        started_by="maria",
    )
    sid = str(row["standup_id"])
    await r.client.start_workflow(
        Standup.run,
        StandupInput(
            tenant_id=tenant,
            standup_id=sid,
            department_id=dept,
            discussion_s=discussion_s,
            assign_next_steps=assign,
        ),
        id=f"standup-{sid}",
        task_queue=r.queue,
        execution_timeout=timedelta(seconds=90),
    )
    return tenant + "/" + sid


async def finish(r: Rig, ref: str) -> Any:
    sid = ref.split("/")[1]
    await asyncio.wait_for(r.client.get_workflow_handle(f"standup-{sid}").result(), 60)
    tenant = ref.split("/")[0]
    return await r.store.get(tenant, sid), await r.conversations.thread(tenant, sid, 0, 500)


async def test_updates_come_from_task_status_and_idle_people_cost_nothing(
    collab_db: Database,
) -> None:
    async with rig(collab_db) as r:
        ref = await start(r)
        standup, thread = await finish(r, ref)
    assert standup["status"] == "done" and standup["led_by"] == "co-ceo"
    reports = {rep["employee_id"]: rep for rep in standup["reports"]}
    assert set(reports) == {"co-ceo", "sales-head", "sales-intern", "marketing"}, "paused: left out"
    assert reports["sales-head"]["done"] == "Booked the Acme demo"
    assert reports["sales-intern"]["idle"] and reports["marketing"]["idle"]
    assert purposes(r.provider) == ["standup-update", "standup-summary"], "one update, one summary"
    update_req = r.provider.requests[0]
    assert "Demo booked for Tuesday" in update_req.messages[0].text
    assert standup["summary"]["where_we_go"] == "Close Acme this month"
    kinds = [m["kind"] for m in thread.messages]
    assert kinds.count("report") == 4 and kinds[-2] == "summary"
    assert thread.conversation["status"] == "closed"
    assert r.tasks.sent("CreateTask") == [], "next steps are not assigned unless asked"


async def test_a_department_standup_covers_that_department(collab_db: Database) -> None:
    async with rig(collab_db) as r:
        standup, _ = await finish(r, await start(r, dept="sales"))
    assert {rep["employee_id"] for rep in standup["reports"]} == {"sales-head", "sales-intern"}
    assert standup["led_by"] == "co-ceo"


async def test_the_person_joins_and_the_co_ceo_answers(collab_db: Database) -> None:
    async with rig(collab_db) as r:
        ref = await start(r, discussion_s=30)
        tenant, sid = ref.split("/")
        for _ in range(100):
            if (await r.store.get(tenant, sid))["status"] == "discussing":
                break
            await asyncio.sleep(0.1)
        person = Who("person", "person", "maria")
        await r.conversations.post(tenant, sid, person, "Focus on Acme first.", uuid.uuid4().hex)
        handle = r.client.get_workflow_handle(f"standup-{sid}")
        await handle.signal(Standup.person_posted)
        for _ in range(100):
            thread = await r.conversations.thread(tenant, sid, 0, 500)
            if any(m["author"] == "co-ceo" and m["kind"] == "message" for m in thread.messages):
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("the co-CEO never answered")
        await handle.signal(Standup.end_discussion)
        standup, thread = await finish(r, ref)
    assert standup["status"] == "done"
    assert purposes(r.provider).count("standup-reply") == 1
    summary_req = r.provider.requests[-1]
    assert "Focus on Acme first." in summary_req.messages[0].text


async def test_next_steps_become_tasks_once_when_asked(collab_db: Database) -> None:
    async with rig(collab_db) as r:
        standup, _ = await finish(r, await start(r, assign=True))
    (created,) = r.tasks.sent("CreateTask")
    assert created.employee_id == "sales-head" and "Send the Acme quote" in created.input
    assert created.idempotency_key.value.endswith(":step:0")
    assert created.tenant.actor.id == "co-ceo"
    steps = standup["summary"]["next_steps"]
    assert [(s["owner"], s["task_id"]) for s in steps] == [
        ("sales-head", "t-sales-head"),
        ("person", ""),
    ]


async def test_without_a_co_ceo_the_standup_fails_and_says_why(collab_db: Database) -> None:
    async with rig(collab_db, org=lambda: chart(with_ceo=False)) as r:
        standup, _ = await finish(r, await start(r))
    assert standup["status"] == "failed" and "co-CEO" in standup["error"]


async def test_events_start_a_standup_once_and_steer_it(collab_db: Database) -> None:
    from wfk_platform.outbox import new_event

    from core_python.collaboration.worker import StandupEvents

    async with rig(collab_db) as r:
        tenant = str(uuid.uuid4())
        row = await r.store.start(
            tenant,
            uuid.uuid4().hex,
            department_id="",
            discussion_s=60,
            assign_next_steps=False,
            started_by="maria",
        )
        sid = str(row["standup_id"])
        events = StandupEvents(r.client, r.queue)
        started = new_event(
            "collab.standup_started",
            SOURCE,
            tenant,
            {
                "standup_id": sid,
                "department_id": "",
                "discussion_s": 60,
                "assign_next_steps": False,
            },
        )
        async with collab_db.tenant_tx(tenant) as conn:
            await events(conn, started)
            await events(conn, started)  # redelivered: still one standup
        handle = r.client.get_workflow_handle(f"standup-{sid}")
        for _ in range(100):
            if await handle.query(Standup.stage) == "discussing":
                break
            await asyncio.sleep(0.1)
        ended = new_event("collab.standup_end_requested", SOURCE, tenant, {"standup_id": sid})
        async with collab_db.tenant_tx(tenant) as conn:
            await events(conn, ended)
        await asyncio.wait_for(handle.result(), 60)
        assert (await r.store.get(tenant, sid))["status"] == "done"
        runs = [w async for w in r.client.list_workflows(f"WorkflowId = 'standup-{sid}'")]
        assert len(runs) == 1
