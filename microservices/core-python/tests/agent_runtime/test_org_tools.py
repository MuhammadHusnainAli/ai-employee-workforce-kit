"""The org tools an employee uses: list the team, hand work down, message and call colleagues,
ask for a hire. Other modules are faked at their gRPC surface."""

from dataclasses import dataclass, field
from typing import Any

import grpc
from agent_harness.tools import Tool, ToolContext

from wfk.collaboration_service.v1 import collaboration_pb2 as collab
from wfk.task_service.v1 import tasks_pb2 as tasks
from wfk.workforce_service.v1 import workforce_pb2 as wf

from core_python.agent_runtime.adapters.org_clients import OrgClients
from core_python.agent_runtime.org_tools import MAX_DELEGATIONS_PER_STEP, org_tools
from core_python.agent_runtime.tool_catalog import CATALOG, build_catalog

TENANT = "0193a7c4-0000-7000-8000-0000000000d1"
RUN = "0193a7c4-0000-7000-8000-00000000aaaa"


def rpc_error(code: grpc.StatusCode, details: str) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details=details)


@dataclass
class Fake:
    """Records every request; answers from `replies` (a response, or an error to raise)."""

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
            return reply

        return method

    def sent(self, name: str) -> list[Any]:
        return [r for n, r in self.calls if n == name]


def chart() -> wf.GetOrgChartResponse:
    def e(eid: str, title: str, manager: str = "", ceo: bool = False) -> wf.Employee:
        return wf.Employee(
            employee_id=eid,
            name=eid.title(),
            title=title,
            manager_id=manager,
            is_co_ceo=ceo,
            department_id="sales" if "sales" in eid else "",
            status=wf.EMPLOYEE_STATUS_ACTIVE,
        )

    return wf.GetOrgChartResponse(
        employees=[
            e("co-ceo", "Co-CEO", ceo=True),
            e("sales-head", "Head of Sales", "co-ceo"),
            e("sales-junior", "Junior Sales", "sales-head"),
            e("marketing", "Head of Marketing", "co-ceo"),
        ],
        departments=[
            wf.Department(department_id="sales", name="Sales", head_employee_id="sales-head")
        ],
    )


def rig(**replies: Any) -> tuple[dict[str, Any], Fake, Fake, Fake]:
    workforce, task, talk = Fake(), Fake(), Fake()
    workforce.replies = {
        "GetOrgChart": chart(),
        "CanDelegate": wf.CanDelegateResponse(allowed=True),
        "GetVersion": wf.GetVersionResponse(version=wf.EmployeeVersion(model="claude-haiku")),
        "RequestHire": wf.RequestHireResponse(request=wf.HireRequest(request_id="req-1")),
    }
    task.replies = {"CreateTask": tasks.CreateTaskResponse(task=tasks.Task(task_id="child-1"))}
    talk.replies = {
        "SendMessage": collab.SendMessageResponse(message=collab.Message(conversation_id="c-1")),
        "ReadInbox": collab.ReadInboxResponse(
            messages=[
                collab.Message(
                    conversation_id="c-1", author="marketing", text="Price sheet is out."
                ),
            ]
        ),
        "AskColleague": collab.AskColleagueResponse(
            conversation_id="call-1", answer="In the drive.", turns_left=5
        ),
    }
    for name, reply in replies.items():
        for fake in (workforce, task, talk):
            if name in fake.replies:
                fake.replies[name] = reply
    tools = {name: make() for name, make in org_tools(OrgClients(workforce, task, talk)).items()}
    return tools, workforce, task, talk


def ctx(employee: str = "sales-head", idx: int = 0) -> ToolContext:
    return ToolContext(
        agent=employee,
        run_id=RUN,
        step=2,
        state={
            "idempotency_key": f"{RUN}:2:{idx}",
            "tenant_id": TENANT,
            "employee_id": employee,
        },
    )


async def run(tool: Any, args: dict[str, Any], c: ToolContext | None = None) -> tuple[str, bool]:
    out = await tool.run("call-x", args, c or ctx())
    return out.content, out.is_error


async def test_delegate_hands_work_down_as_a_sub_task() -> None:
    tools, workforce, task, _ = rig()
    text, err = await run(
        tools["delegate_task"],
        {
            "employee_id": "sales-junior",
            "title": "Call Acme",
            "instructions": "Book a demo.",
        },
    )
    assert not err and "child-1" in text and "wait_for_reports" in text
    (check,) = workforce.sent("CanDelegate")
    assert (check.from_employee, check.to_employee) == ("sales-head", "sales-junior")
    (req,) = task.sent("CreateTask")
    assert req.parent_task_id == RUN and req.employee_id == "sales-junior"
    assert req.idempotency_key.value == f"{RUN}:2:0"
    assert req.tenant.actor.id == "sales-head" and req.tenant.tenant_id == TENANT
    assert "Book a demo." in req.input and "sales-head" in req.input


async def test_delegate_outside_the_chart_is_refused_with_the_reason() -> None:
    no = wf.CanDelegateResponse(allowed=False, reason="marketing is not below you")
    tools, _, task, _ = rig(CanDelegate=no)
    text, err = await run(
        tools["delegate_task"],
        {
            "employee_id": "marketing",
            "title": "x",
            "instructions": "y",
        },
    )
    assert err and "marketing is not below you" in text
    assert task.sent("CreateTask") == []


async def test_at_most_ten_delegations_in_one_step() -> None:
    tools, _, task, _ = rig()
    args = {"employee_id": "sales-junior", "title": "x", "instructions": "y"}
    text, err = await run(tools["delegate_task"], args, ctx(idx=MAX_DELEGATIONS_PER_STEP))
    assert err and str(MAX_DELEGATIONS_PER_STEP) in text
    assert task.sent("CreateTask") == []
    assert MAX_DELEGATIONS_PER_STEP == 10


async def test_a_refused_sub_task_becomes_a_readable_error() -> None:
    deep = rpc_error(grpc.StatusCode.FAILED_PRECONDITION, "sub-tasks go at most 3 levels deep")
    tools, *_ = rig(CreateTask=deep)
    text, err = await run(
        tools["delegate_task"],
        {
            "employee_id": "sales-junior",
            "title": "x",
            "instructions": "y",
        },
    )
    assert err and "3 levels deep" in text


async def test_list_team_shows_where_you_sit() -> None:
    tools, *_ = rig()
    text, err = await run(tools["list_team"], {})
    assert not err
    assert "sales-head (Head of Sales)" in text
    assert "reports to: co-ceo" in text.lower()
    assert "sales-junior" in text and "marketing" in text


async def test_messages_and_calls() -> None:
    tools, _, _, talk = rig()
    text, err = await run(tools["send_message"], {"to": "marketing", "text": "Need the sheet."})
    assert not err and "marketing" in text
    (sent,) = talk.sent("SendMessage")
    assert sent.tenant.actor.id == "sales-head" and sent.idempotency_key.value == f"{RUN}:2:0"

    text, _ = await run(tools["read_messages"], {})
    assert "marketing" in text and "Price sheet is out." in text

    text, err = await run(
        tools["ask_colleague"],
        {
            "employee_id": "marketing",
            "question": "Where is the sheet?",
            "call_id": "call-1",
        },
    )
    assert not err and "In the drive." in text and "call-1" in text and "5" in text
    (asked,) = talk.sent("AskColleague")
    assert (asked.colleague, asked.conversation_id) == ("marketing", "call-1")


async def test_request_hire_waits_for_the_person() -> None:
    tools, workforce, _, _ = rig()
    text, err = await run(
        tools["request_hire"],
        {
            "employee_id": "sales-intern",
            "name": "Sam",
            "title": "Sales Intern",
            "role": "Qualifies leads",
            "reason": "Too many inbound leads",
            "manager_id": "sales-head",
            "department_id": "sales",
        },
        ctx("co-ceo"),
    )
    assert not err and "req-1" in text and "person" in text.lower()
    (req,) = workforce.sent("RequestHire")
    assert req.hire.model == "claude-haiku", "the requester's own model by default"
    assert req.hire.manager_id == "sales-head" and req.reason == "Too many inbound leads"


def test_catalog_has_org_tools_only_with_clients() -> None:
    assert set(build_catalog(None)) == set(CATALOG)
    with_org = build_catalog(OrgClients(Fake(), Fake(), Fake()))
    assert {"delegate_task", "wait_for_reports", "ask_colleague", "request_hire"} <= set(with_org)


async def test_practice_runs_change_nothing_in_the_company() -> None:
    tools, workforce, task, talk = rig()
    practice = ctx()
    practice.state["mode"] = "eval"
    for name, args in [
        ("delegate_task", {"employee_id": "sales-junior", "title": "x", "instructions": "y"}),
        ("send_message", {"to": "marketing", "text": "hi"}),
        ("ask_colleague", {"employee_id": "marketing", "question": "?"}),
        (
            "request_hire",
            {"employee_id": "x", "name": "X", "title": "t", "role": "r", "reason": "r"},
        ),
    ]:
        text, err = await run(tools[name], args, practice)
        assert err and "practice" in text, name
    assert task.calls == [] and talk.calls == [] and workforce.sent("RequestHire") == []


async def test_act_tells_tools_the_run_mode() -> None:
    from agent_harness.types import ToolUseBlock

    from core_python.agent_runtime.harness.act import act
    from core_python.agent_runtime.harness.state import AgentState, EmployeeSpec

    seen: dict[str, object] = {}

    def probe(ctx: ToolContext) -> str:
        """Report the context."""
        seen.update(ctx.state)
        return "ok"

    spec = EmployeeSpec.build(
        name="e", instructions="", model="m", tools=[Tool(probe)], tiers={"probe": "R0"}
    )
    state = AgentState.start(run_id="r", tenant_id=TENANT, employee_id="e", task="t")
    state = state.model_copy(update={"mode": "shadow"})
    await act(spec, state, [ToolUseBlock(id="1", name="probe", input={})])
    assert seen["mode"] == "shadow"
