"""WorkforceService over real gRPC, on a migrated workforce_db (just up)."""

import uuid
from collections.abc import AsyncIterator

import grpc
import psycopg
import pytest

from wfk.common.v1 import common_pb2
from wfk.workforce_service.v1 import workforce_pb2 as pb
from wfk.workforce_service.v1 import workforce_pb2_grpc
from wfk_platform.db import Database

from workforce_service.app import create_server
from workforce_service.settings import Settings

from ..conftest import OTHER_TENANT, TENANT, WORKFORCE_DB

pytestmark = pytest.mark.integration
Stub = workforce_pb2_grpc.WorkforceServiceStub


@pytest.fixture
async def stub(workforce_db: Database) -> AsyncIterator[Stub]:
    server, _, port = create_server(Settings(host="127.0.0.1"), workforce_db, port=0)
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield workforce_pb2_grpc.WorkforceServiceStub(channel)
    await server.stop(grace=None)


def tenant(t: str = TENANT) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(
        tenant_id=t, actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_HUMAN, id="maria")
    )


def new_id() -> str:
    return f"sam-{uuid.uuid4().hex[:8]}"


def publish(emp: str, key: str, **kw: object) -> pb.PublishVersionRequest:
    base: dict[str, object] = {
        "role": "Support specialist",
        "instructions": "Answer order questions.",
        "model": "claude-haiku",
        "tool_grants": [
            pb.ToolGrant(tool="orders.lookup", tier=pb.RISK_TIER_R0),
            pb.ToolGrant(tool="email.send", tier=pb.RISK_TIER_R2),
        ],
        "limits": pb.Limits(max_steps=12, max_cost_usd="0.50"),
    }
    base.update(kw)
    return pb.PublishVersionRequest(
        tenant=tenant(), employee_id=emp,
        idempotency_key=common_pb2.IdempotencyKey(value=key), **base,  # type: ignore[arg-type]
    )  # fmt: skip


async def code(call: object) -> grpc.StatusCode:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await call  # type: ignore[misc]
    return caught.value.code()


async def events(db: Database, employee_id: str) -> list[str]:
    async with db.tx() as conn:
        cur = await conn.execute(
            "SELECT type FROM outbox WHERE subject = %s ORDER BY seq", (employee_id,)
        )
        return [r["type"] for r in await cur.fetchall()]


async def test_create_is_idempotent_and_conflicts_are_refused(stub: Stub) -> None:
    emp = new_id()
    first = await stub.CreateEmployee(
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id=emp, name="Sam")
    )
    again = await stub.CreateEmployee(
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id=emp, name="Sam")
    )
    assert first.employee == again.employee
    assert first.employee.status == pb.EMPLOYEE_STATUS_ACTIVE and first.employee.active_version == 0
    clash = pb.CreateEmployeeRequest(tenant=tenant(), employee_id=emp, name="Someone else")
    assert await code(stub.CreateEmployee(clash)) == grpc.StatusCode.ALREADY_EXISTS


async def test_versions_are_published_pinned_and_rolled_back(
    stub: Stub, workforce_db: Database
) -> None:
    emp = new_id()
    await stub.CreateEmployee(
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id=emp, name="Sam")
    )
    v1 = (await stub.PublishVersion(publish(emp, f"{emp}-1"))).version
    v2 = (await stub.PublishVersion(publish(emp, f"{emp}-2", instructions="Be brief."))).version
    assert (v1.version, v2.version) == (1, 2)
    assert [g.tool for g in v1.tool_grants] == ["orders.lookup", "email.send"]
    assert v1.limits.max_cost_usd == "0.50" and v1.created_by == "maria"

    active = await stub.GetVersion(pb.GetVersionRequest(tenant=tenant(), employee_id=emp))
    assert active.version.version == 2 and active.version.instructions == "Be brief."
    pinned = await stub.GetVersion(
        pb.GetVersionRequest(tenant=tenant(), employee_id=emp, version=1)
    )
    assert pinned.version == v1, "an old version never changes"

    rolled = await stub.SetActiveVersion(
        pb.SetActiveVersionRequest(tenant=tenant(), employee_id=emp, version=1)
    )
    assert rolled.employee.active_version == 1
    missing = pb.SetActiveVersionRequest(tenant=tenant(), employee_id=emp, version=9)
    assert await code(stub.SetActiveVersion(missing)) == grpc.StatusCode.NOT_FOUND
    assert await events(workforce_db, emp) == [
        "employee.created", "employee.version_published", "employee.version_published",
        "employee.version_activated",
    ]  # fmt: skip


async def test_publish_is_idempotent(stub: Stub, workforce_db: Database) -> None:
    emp = new_id()
    await stub.CreateEmployee(
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id=emp, name="Sam")
    )
    a = await stub.PublishVersion(publish(emp, f"{emp}-same"))
    b = await stub.PublishVersion(publish(emp, f"{emp}-same"))
    assert a.version == b.version and a.version.version == 1
    assert (await events(workforce_db, emp)).count("employee.version_published") == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"auto_approve": ["orders.lookup"]},  # only R2 can be auto-approved
        {"auto_approve": ["refund.issue"]},  # not granted
        {"tool_grants": [pb.ToolGrant(tool="x.y", tier=pb.RISK_TIER_R0)] * 2},  # granted twice
        {"tool_grants": [pb.ToolGrant(tool="x.y")]},  # no tier
        {"model": ""},
        {"limits": pb.Limits(max_steps=500)},
        {"limits": pb.Limits(max_cost_usd="lots")},
    ],
)
async def test_invalid_versions_are_rejected(stub: Stub, bad: dict[str, object]) -> None:
    emp = new_id()
    await stub.CreateEmployee(
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id=emp, name="Sam")
    )
    assert await code(stub.PublishVersion(publish(emp, f"{emp}-bad", **bad))) == (
        grpc.StatusCode.INVALID_ARGUMENT
    )


async def test_versions_cannot_be_edited_even_directly_in_the_database(stub: Stub) -> None:
    emp = new_id()
    await stub.CreateEmployee(
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id=emp, name="Sam")
    )
    await stub.PublishVersion(publish(emp, f"{emp}-1"))
    with psycopg.connect(WORKFORCE_DB) as c:  # the superuser, bypassing the service entirely
        with pytest.raises(psycopg.errors.RestrictViolation, match="immutable"):
            c.execute(
                "UPDATE employee_versions SET instructions = 'evil' WHERE employee_id = %s", (emp,)
            )
        c.rollback()
        with pytest.raises(psycopg.errors.RestrictViolation, match="immutable"):
            c.execute("DELETE FROM employee_versions WHERE employee_id = %s", (emp,))


async def test_pause_and_resume(stub: Stub, workforce_db: Database) -> None:
    emp = new_id()
    await stub.CreateEmployee(
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id=emp, name="Sam")
    )
    paused = await stub.PauseEmployee(pb.PauseEmployeeRequest(tenant=tenant(), employee_id=emp))
    again = await stub.PauseEmployee(pb.PauseEmployeeRequest(tenant=tenant(), employee_id=emp))
    assert paused.employee.status == again.employee.status == pb.EMPLOYEE_STATUS_PAUSED
    resumed = await stub.ResumeEmployee(pb.ResumeEmployeeRequest(tenant=tenant(), employee_id=emp))
    assert resumed.employee.status == pb.EMPLOYEE_STATUS_ACTIVE
    assert await events(workforce_db, emp) == [
        "employee.created", "employee.paused", "employee.resumed",
    ]  # fmt: skip


async def test_tenants_and_bad_requests(stub: Stub) -> None:
    emp = new_id()
    await stub.CreateEmployee(
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id=emp, name="Sam")
    )
    other = pb.GetEmployeeRequest(tenant=tenant(OTHER_TENANT), employee_id=emp)
    assert await code(stub.GetEmployee(other)) == grpc.StatusCode.NOT_FOUND
    listed = await stub.ListEmployees(pb.ListEmployeesRequest(tenant=tenant(OTHER_TENANT)))
    assert emp not in {e.employee_id for e in listed.employees}

    for bad in (
        pb.CreateEmployeeRequest(tenant=tenant("acme"), employee_id="sam-x", name="Sam"),
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id="Sam X", name="Sam"),
        pb.CreateEmployeeRequest(tenant=tenant(), employee_id="sam-y", name="  "),
    ):
        assert await code(stub.CreateEmployee(bad)) == grpc.StatusCode.INVALID_ARGUMENT
    no_key = publish(emp, "")
    assert await code(stub.PublishVersion(no_key)) == grpc.StatusCode.INVALID_ARGUMENT
    unpublished = pb.GetVersionRequest(tenant=tenant(), employee_id=emp)
    assert await code(stub.GetVersion(unpublished)) == grpc.StatusCode.NOT_FOUND
