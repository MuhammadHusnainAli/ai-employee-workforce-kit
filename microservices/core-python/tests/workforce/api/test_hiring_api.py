"""Hiring over real gRPC, on a migrated workforce_db (just up)."""

import uuid

import grpc
import pytest

from wfk.common.v1 import common_pb2
from wfk.workforce_service.v1 import workforce_pb2 as pb
from wfk_platform.db import Database

from .test_workforce_api import Stub, code
from .test_workforce_api import stub as stub  # the fixture

pytestmark = pytest.mark.integration


def person(t: str) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(
        tenant_id=t, actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_HUMAN, id="owner")
    )


def employee(t: str, eid: str = "co-ceo") -> common_pb2.TenantContext:
    return common_pb2.TenantContext(
        tenant_id=t,
        employee_id=eid,
        actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_EMPLOYEE, id=eid),
    )


def key(v: str = "") -> common_pb2.IdempotencyKey:
    return common_pb2.IdempotencyKey(value=v or uuid.uuid4().hex)


def hire(eid: str, **kw: object) -> pb.Hire:
    base: dict[str, object] = {
        "employee_id": eid,
        "name": eid.title(),
        "role": "Sales",
        "model": "local-mock",
    }
    base.update(kw)
    return pb.Hire(**base)  # type: ignore[arg-type]


async def company(s: Stub) -> str:
    """A fresh tenant with a co-CEO and a sales department."""
    t = str(uuid.uuid4())
    await s.CreateDepartment(
        pb.CreateDepartmentRequest(tenant=person(t), department_id="sales", name="Sales")
    )
    await s.HireEmployee(
        pb.HireEmployeeRequest(
            tenant=person(t),
            idempotency_key=key(),
            hire=hire("co-ceo", title="Co-CEO", is_co_ceo=True, role="Runs the company"),
        )
    )
    return t


async def test_a_person_hires_directly_with_default_tools(stub: Stub) -> None:
    t = await company(stub)
    k = key()
    req = pb.HireEmployeeRequest(
        tenant=person(t),
        idempotency_key=k,
        hire=hire(
            "sales-head",
            title="Head of Sales",
            department_id="sales",
            manager_id="co-ceo",
            department_head=True,
            max_cost_usd="0.40",
        ),
    )
    got = await stub.HireEmployee(req)
    assert (got.employee.title, got.employee.manager_id) == ("Head of Sales", "co-ceo")
    assert got.version.version == 1 and got.version.limits.max_cost_usd == "0.40"
    assert "delegate_task" in {g.tool for g in got.version.tool_grants}
    assert "request_hire" not in {g.tool for g in got.version.tool_grants}
    again = await stub.HireEmployee(req)
    assert again.version.version == 1
    ceo = await stub.GetVersion(pb.GetVersionRequest(tenant=person(t), employee_id="co-ceo"))
    assert "request_hire" in {g.tool for g in ceo.version.tool_grants}
    chart = await stub.GetOrgChart(pb.GetOrgChartRequest(tenant=person(t)))
    assert chart.departments[0].head_employee_id == "sales-head"


async def test_only_a_person_hires(stub: Stub) -> None:
    t = await company(stub)
    req = pb.HireEmployeeRequest(tenant=employee(t), idempotency_key=key(), hire=hire("intern"))
    assert await code(stub.HireEmployee(req)) == grpc.StatusCode.PERMISSION_DENIED


async def test_a_bad_hire_creates_nothing(stub: Stub) -> None:
    t = await company(stub)
    bad = hire("intern", manager_id="ghost")
    req = pb.HireEmployeeRequest(tenant=person(t), idempotency_key=key(), hire=bad)
    assert await code(stub.HireEmployee(req)) == grpc.StatusCode.INVALID_ARGUMENT
    assert (
        await code(stub.GetEmployee(pb.GetEmployeeRequest(tenant=person(t), employee_id="intern")))
        == grpc.StatusCode.NOT_FOUND
    )


async def test_an_approved_request_hires_and_a_rejected_one_does_not(
    stub: Stub, workforce_db: Database
) -> None:
    t = await company(stub)
    k = key()
    asked = await stub.RequestHire(
        pb.RequestHireRequest(
            tenant=employee(t),
            idempotency_key=k,
            reason="Too many leads",
            hire=hire(
                "sales-junior", title="Junior Sales", department_id="sales", manager_id="co-ceo"
            ),
        )
    )
    req = asked.request
    assert req.status == pb.HIRE_REQUEST_STATUS_PENDING and req.requested_by == "co-ceo"
    again = await stub.RequestHire(
        pb.RequestHireRequest(tenant=employee(t), idempotency_key=k, hire=req.hire)
    )
    assert again.request.request_id == req.request_id
    # Asking created nobody.
    assert (
        await code(
            stub.GetEmployee(pb.GetEmployeeRequest(tenant=person(t), employee_id="sales-junior"))
        )
        == grpc.StatusCode.NOT_FOUND
    )

    # An employee cannot approve its own request.
    decide = pb.DecideHireRequestRequest(
        tenant=employee(t), request_id=req.request_id, approved=True
    )
    assert await code(stub.DecideHireRequest(decide)) == grpc.StatusCode.PERMISSION_DENIED

    done = await stub.DecideHireRequest(
        pb.DecideHireRequestRequest(
            tenant=person(t), request_id=req.request_id, approved=True, note="Go ahead"
        )
    )
    assert done.request.status == pb.HIRE_REQUEST_STATUS_APPROVED
    assert done.request.decided_by == "owner"
    assert done.employee.manager_id == "co-ceo"
    twice = pb.DecideHireRequestRequest(tenant=person(t), request_id=req.request_id, approved=False)
    assert await code(stub.DecideHireRequest(twice)) == grpc.StatusCode.FAILED_PRECONDITION

    no = await stub.RequestHire(
        pb.RequestHireRequest(tenant=employee(t), idempotency_key=key(), hire=hire("intern"))
    )
    rejected = await stub.DecideHireRequest(
        pb.DecideHireRequestRequest(
            tenant=person(t), request_id=no.request.request_id, approved=False, note="Not now"
        )
    )
    assert rejected.request.status == pb.HIRE_REQUEST_STATUS_REJECTED
    assert not rejected.HasField("employee")
    pending = await stub.ListHireRequests(
        pb.ListHireRequestsRequest(tenant=person(t), status=pb.HIRE_REQUEST_STATUS_PENDING)
    )
    assert list(pending.requests) == []
    listed = await stub.ListHireRequests(pb.ListHireRequestsRequest(tenant=person(t)))
    assert len(listed.requests) == 2

    async with workforce_db.tx() as conn:
        cur = await conn.execute(
            "SELECT type FROM outbox WHERE tenant_id = %s AND type LIKE 'employee.hire%%'"
            " ORDER BY seq",
            (t,),
        )
        assert [r["type"] for r in await cur.fetchall()] == [
            "employee.hire_requested",
            "employee.hire_decided",
            "employee.hire_requested",
            "employee.hire_decided",
        ]


async def test_approving_a_hire_that_no_longer_fits_fails_and_stays_pending(stub: Stub) -> None:
    t = await company(stub)
    asked = await stub.RequestHire(
        pb.RequestHireRequest(
            tenant=employee(t), idempotency_key=key(), hire=hire("dup", is_co_ceo=True)
        )
    )
    decide = pb.DecideHireRequestRequest(
        tenant=person(t), request_id=asked.request.request_id, approved=True
    )
    assert await code(stub.DecideHireRequest(decide)) == grpc.StatusCode.INVALID_ARGUMENT
    listed = await stub.ListHireRequests(
        pb.ListHireRequestsRequest(tenant=person(t), status=pb.HIRE_REQUEST_STATUS_PENDING)
    )
    assert [r.request_id for r in listed.requests] == [asked.request.request_id]
