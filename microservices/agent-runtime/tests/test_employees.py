"""Employees from workforce-service: versions as specs, pinning, paused, unknown tools, fallback.

A stand-in WorkforceService (from this service's copy of the contract) serves the versions."""

from collections.abc import AsyncIterator

import grpc
import pytest
from agent_harness.tools import Tool

from wfk.workforce_service.v1 import workforce_pb2 as pb
from wfk.workforce_service.v1 import workforce_pb2_grpc

from agent_runtime.employees import (
    ChainDirectory,
    EmployeeNotFound,
    EmployeeUnavailable,
    StaticDirectory,
    WorkforceDirectory,
    spec_from_version,
)
from agent_runtime.harness.state import EmployeeSpec
from agent_runtime.tool_catalog import CATALOG

TENANT = "0193a7c4-0000-7000-8000-000000000001"


def version(n: int, instructions: str) -> pb.EmployeeVersion:
    return pb.EmployeeVersion(
        employee_id="ana", version=n, role="Support specialist for Acme.",
        instructions=instructions, model="claude-haiku",
        tool_grants=[pb.ToolGrant(tool="word_count", tier=pb.RISK_TIER_R0),
                     pb.ToolGrant(tool="send_email", tier=pb.RISK_TIER_R2)],
        auto_approve=["send_email"],
        limits=pb.Limits(max_steps=7, max_cost_usd="0.50"),
    )  # fmt: skip


class Workforce(workforce_pb2_grpc.WorkforceServiceServicer):
    def __init__(self) -> None:
        self.versions = {1: version(1, "Be brief."), 2: version(2, "Be very brief.")}
        self.active = 1
        self.paused = False
        self.calls = 0

    async def GetVersion(self, request, context):
        self.calls += 1
        if request.employee_id != "ana":
            await context.abort(grpc.StatusCode.NOT_FOUND, "no such employee")
        v = self.versions[request.version or self.active]
        status = pb.EMPLOYEE_STATUS_PAUSED if self.paused else pb.EMPLOYEE_STATUS_ACTIVE
        return pb.GetVersionResponse(version=v, status=status)

    async def GetEmployee(self, request, context):
        return pb.GetEmployeeResponse(employee=pb.Employee(employee_id="ana", name="Ana"))


@pytest.fixture
async def served() -> AsyncIterator[tuple[Workforce, str]]:
    server = grpc.aio.server()
    fake = Workforce()
    workforce_pb2_grpc.add_WorkforceServiceServicer_to_server(fake, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    yield fake, f"127.0.0.1:{port}"
    await server.stop(None)


def test_a_version_becomes_a_spec() -> None:
    spec = spec_from_version(version(3, "Be brief."), "Ana", CATALOG)
    assert spec.name == "Ana" and spec.version == 3 and spec.model == "claude-haiku"
    assert spec.instructions == "Support specialist for Acme.\n\nBe brief."
    assert spec.tools.names == ["word_count"], "a granted tool that does not exist is left out"
    assert spec.tiers == {"word_count": "R0"}
    assert (spec.limits.max_steps, spec.limits.max_cost_usd) == (7, 0.5)
    assert spec.auto_approve == {"send_email"}


async def test_runs_pin_the_version_they_start_with(served: tuple[Workforce, str]) -> None:
    fake, address = served
    d = WorkforceDirectory(address, CATALOG)
    first = await d.get("ana", tenant_id=TENANT)  # load_employee: the active version
    assert first.version == 1
    fake.active = 2  # a new version is published mid-run
    again = await d.get("ana", tenant_id=TENANT, version=first.version)
    assert (again.version, again.instructions.endswith("Be brief.")) == (1, True)
    calls = fake.calls
    await d.get("ana", tenant_id=TENANT, version=1)
    assert fake.calls == calls, "pinned versions are cached: they never change"
    assert (await d.get("ana", tenant_id=TENANT)).version == 2, "a new run gets the new one"


async def test_paused_and_unknown_employees(served: tuple[Workforce, str]) -> None:
    fake, address = served
    d = WorkforceDirectory(address, CATALOG)
    fake.paused = True
    with pytest.raises(EmployeeUnavailable):
        await d.get("ana", tenant_id=TENANT)
    with pytest.raises(EmployeeNotFound):
        await d.get("bob", tenant_id=TENANT)
    with pytest.raises(EmployeeNotFound):
        await d.get("ana")  # no tenant, no lookup


async def test_the_chain_falls_back_to_employees_in_code(served: tuple[Workforce, str]) -> None:
    _, address = served

    def summariser() -> EmployeeSpec:
        return EmployeeSpec.build(name="Sam", instructions="Summarise.", model="m",
                                  tools=[Tool(CATALOG["word_count"]().fn)])  # fmt: skip

    chain = ChainDirectory(
        WorkforceDirectory(address, CATALOG), StaticDirectory({"summariser": summariser})
    )
    assert (await chain.get("ana", tenant_id=TENANT)).name == "Ana"
    assert (await chain.get("summariser", tenant_id=TENANT)).name == "Sam"
    with pytest.raises(EmployeeNotFound):
        await chain.get("nobody", tenant_id=TENANT)
