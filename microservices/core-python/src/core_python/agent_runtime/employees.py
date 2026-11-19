"""Where an employee's definition comes from at run time.

- `WorkforceDirectory`: the employee's published versions in workforce-service. A run pins the
  version it starts with (`load_employee` resolves the active one); every later step asks for
  exactly that version, which never changes, so it is cached.
- `StaticDirectory`: employees defined in code (the demo `summariser`, tests).
- `ChainDirectory`: the first directory that knows the employee.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Protocol

import grpc
from agent_harness.tools import Tool

from wfk.common.v1 import common_pb2
from wfk.workforce_service.v1 import workforce_pb2, workforce_pb2_grpc

from core_python.agent_runtime.harness.state import EmployeeSpec, Limits, Tier

log = logging.getLogger(__name__)


class EmployeeNotFound(Exception):
    pass


class EmployeeUnavailable(Exception):
    """The employee exists but must not run now (paused)."""


class EmployeeDirectory(Protocol):
    async def get(self, employee_id: str, *, tenant_id: str = "", version: int = 0) -> EmployeeSpec:
        """The employee as of `version` (0: the active one). The spec's `version` says which."""
        ...


class StaticDirectory:
    """Employees defined in code. Each get() builds a fresh spec, like a fresh worker would."""

    def __init__(self, factories: dict[str, Callable[[], EmployeeSpec]]) -> None:
        self.factories = factories

    async def get(self, employee_id: str, *, tenant_id: str = "", version: int = 0) -> EmployeeSpec:
        try:
            return self.factories[employee_id]()
        except KeyError:
            raise EmployeeNotFound(f"no employee {employee_id!r}") from None


class ChainDirectory:
    def __init__(self, *directories: EmployeeDirectory) -> None:
        self.directories = directories

    async def get(self, employee_id: str, *, tenant_id: str = "", version: int = 0) -> EmployeeSpec:
        for d in self.directories:
            try:
                return await d.get(employee_id, tenant_id=tenant_id, version=version)
            except EmployeeNotFound:
                continue
        raise EmployeeNotFound(f"no employee {employee_id!r}")


_TIERS: dict[int, Tier] = {
    workforce_pb2.RISK_TIER_R0: "R0",
    workforce_pb2.RISK_TIER_R1: "R1",
    workforce_pb2.RISK_TIER_R2: "R2",
    workforce_pb2.RISK_TIER_R3: "R3",
}


def spec_from_version(
    v: workforce_pb2.EmployeeVersion, name: str, catalog: dict[str, Callable[[], Tool]]
) -> EmployeeSpec:
    """One published version as the harness's EmployeeSpec. Granted tools this runtime cannot
    execute are left out (and logged): an employee never gets a tool that does not exist."""
    tools: list[Tool] = []
    tiers: dict[str, Tier] = {}
    for g in v.tool_grants:
        make = catalog.get(g.tool)
        if make is None:
            log.warning("granted tool not available here", extra={"tool": g.tool})
            continue
        tools.append(make())
        tiers[g.tool] = _TIERS.get(g.tier, "R1")
    limits = Limits()
    if v.limits.max_steps > 0:
        limits.max_steps = v.limits.max_steps
    try:
        if v.limits.max_cost_usd and Decimal(v.limits.max_cost_usd) > 0:
            limits.max_cost_usd = float(Decimal(v.limits.max_cost_usd))
    except InvalidOperation:
        pass
    instructions = f"{v.role.strip()}\n\n{v.instructions.strip()}".strip()
    spec = EmployeeSpec.build(
        name=name or v.employee_id,
        instructions=instructions,
        model=v.model,
        tools=tools,
        tiers=tiers,
        limits=limits,
    )
    spec.auto_approve = set(v.auto_approve)
    spec.version = v.version
    return spec


class WorkforceDirectory:
    """Published versions from workforce-service (GetVersion, GetEmployee)."""

    def __init__(
        self,
        address: str,
        catalog: dict[str, Callable[[], Tool]],
        *,
        deadline_s: float = 5.0,
    ) -> None:
        self.channel = grpc.aio.insecure_channel(address)
        self.stub = workforce_pb2_grpc.WorkforceServiceStub(self.channel)
        self.catalog = catalog
        self.deadline_s = deadline_s
        #: (tenant, employee, version) -> (version message, name). Versions never change.
        self.cache: dict[tuple[str, str, int], tuple[workforce_pb2.EmployeeVersion, str]] = {}

    async def get(self, employee_id: str, *, tenant_id: str = "", version: int = 0) -> EmployeeSpec:
        if not tenant_id:
            raise EmployeeNotFound(f"no employee {employee_id!r} without a tenant")
        cached = self.cache.get((tenant_id, employee_id, version)) if version else None
        if cached is None:
            tenant = common_pb2.TenantContext(tenant_id=tenant_id)
            try:
                got = await self.stub.GetVersion(
                    workforce_pb2.GetVersionRequest(
                        tenant=tenant, employee_id=employee_id, version=version
                    ),
                    timeout=self.deadline_s,
                )
                emp = await self.stub.GetEmployee(
                    workforce_pb2.GetEmployeeRequest(tenant=tenant, employee_id=employee_id),
                    timeout=self.deadline_s,
                )
            except grpc.aio.AioRpcError as exc:
                if exc.code() in (grpc.StatusCode.NOT_FOUND, grpc.StatusCode.FAILED_PRECONDITION):
                    raise EmployeeNotFound(f"no employee {employee_id!r}") from exc
                raise  # unavailable: the activity retries
            if version == 0 and got.status == workforce_pb2.EMPLOYEE_STATUS_PAUSED:
                raise EmployeeUnavailable(f"employee {employee_id!r} is paused")
            cached = (got.version, emp.employee.name)
            self.cache[(tenant_id, employee_id, got.version.version)] = cached
        return spec_from_version(cached[0], cached[1], self.catalog)
