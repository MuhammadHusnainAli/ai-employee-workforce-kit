"""The org chart over real gRPC, on a migrated workforce_db (just up)."""

import uuid

import grpc
import pytest

from wfk.workforce_service.v1 import workforce_pb2 as pb
from wfk_platform.db import Database

from ..conftest import OTHER_TENANT, TENANT
from .test_workforce_api import Stub, code, tenant
from .test_workforce_api import stub as stub  # the fixture

pytestmark = pytest.mark.integration


# Each test gets its own tenant: the co-CEO is one per tenant.
def fresh_tenant() -> str:
    return str(uuid.uuid4())


async def hire(s: Stub, t: str, eid: str, **place: object) -> pb.Employee:
    await s.CreateEmployee(pb.CreateEmployeeRequest(tenant=tenant(t), employee_id=eid, name=eid))
    got = await s.PlaceEmployee(pb.PlaceEmployeeRequest(tenant=tenant(t), employee_id=eid, **place))  # type: ignore[arg-type]
    return got.employee


async def test_departments_and_reporting_lines_make_an_org_chart(stub: Stub) -> None:
    t = fresh_tenant()
    dept = await stub.CreateDepartment(
        pb.CreateDepartmentRequest(tenant=tenant(t), department_id="sales", name="Sales")
    )
    assert dept.department.name == "Sales"
    again = await stub.CreateDepartment(
        pb.CreateDepartmentRequest(tenant=tenant(t), department_id="sales", name="Sales")
    )
    assert again.department.department_id == "sales"
    assert (
        await code(
            stub.CreateDepartment(
                pb.CreateDepartmentRequest(tenant=tenant(t), department_id="sales", name="Selling")
            )
        )
        == grpc.StatusCode.ALREADY_EXISTS
    )

    ceo = await hire(stub, t, "co-ceo", title="Co-CEO", is_co_ceo=True)
    assert ceo.is_co_ceo and ceo.manager_id == ""
    head = await hire(
        stub,
        t,
        "sales-head",
        title="Head of Sales",
        department_id="sales",
        manager_id="co-ceo",
        department_head=True,
    )
    assert (head.title, head.department_id, head.manager_id) == ("Head of Sales", "sales", "co-ceo")
    await hire(
        stub,
        t,
        "sales-junior",
        title="Junior Sales",
        department_id="sales",
        manager_id="sales-head",
    )

    chart = await stub.GetOrgChart(pb.GetOrgChartRequest(tenant=tenant(t)))
    by_id = {e.employee_id: e for e in chart.employees}
    assert by_id["sales-junior"].manager_id == "sales-head"
    assert [(d.department_id, d.head_employee_id) for d in chart.departments] == [
        ("sales", "sales-head")
    ]
    listed = await stub.ListDepartments(pb.ListDepartmentsRequest(tenant=tenant(t)))
    assert [d.department_id for d in listed.departments] == ["sales"]


async def test_bad_placements_are_refused(stub: Stub) -> None:
    t = fresh_tenant()
    await hire(stub, t, "co-ceo", is_co_ceo=True)
    await hire(stub, t, "head", manager_id="co-ceo")
    await hire(stub, t, "junior", manager_id="head")

    def place(eid: str, **kw: object) -> object:
        return stub.PlaceEmployee(pb.PlaceEmployeeRequest(tenant=tenant(t), employee_id=eid, **kw))  # type: ignore[arg-type]

    assert await code(place("head", manager_id="junior")) == grpc.StatusCode.INVALID_ARGUMENT
    assert await code(place("junior", is_co_ceo=True)) == grpc.StatusCode.INVALID_ARGUMENT
    assert await code(place("junior", manager_id="ghost")) == grpc.StatusCode.INVALID_ARGUMENT
    assert await code(place("junior", department_id="nowhere")) == grpc.StatusCode.INVALID_ARGUMENT
    assert await code(place("ghost")) == grpc.StatusCode.NOT_FOUND
    assert await code(place("junior", department_head=True)) == grpc.StatusCode.INVALID_ARGUMENT


async def test_placing_emits_an_event_and_other_tenants_see_nothing(
    stub: Stub,
    workforce_db: Database,
) -> None:
    eid = f"placed-{uuid.uuid4().hex[:8]}"
    await hire(stub, TENANT, eid, title="Analyst")
    async with workforce_db.tx() as conn:
        cur = await conn.execute("SELECT type FROM outbox WHERE subject = %s ORDER BY seq", (eid,))
        assert [r["type"] for r in await cur.fetchall()] == ["employee.created", "employee.placed"]
    other = await stub.GetOrgChart(pb.GetOrgChartRequest(tenant=tenant(OTHER_TENANT)))
    assert eid not in {e.employee_id for e in other.employees}
