"""Who a colleague is, from the workforce module (GetEmployee, GetVersion)."""

from __future__ import annotations

from typing import Protocol

import grpc

from wfk.common.v1 import common_pb2
from wfk.workforce_service.v1 import workforce_pb2, workforce_pb2_grpc

from core_python.collaboration.domain.conversations import Colleague


class Colleagues(Protocol):
    async def get(self, tenant_id: str, employee_id: str) -> Colleague | None:
        """The employee, or None when there is no such employee or it has no version yet."""
        ...


class StaticColleagues:
    """Colleagues defined in code (tests)."""

    def __init__(self, people: dict[str, Colleague]) -> None:
        self.people = people

    async def get(self, tenant_id: str, employee_id: str) -> Colleague | None:
        return self.people.get(employee_id)


class WorkforceColleagues:
    def __init__(self, address: str, *, deadline_s: float = 5.0) -> None:
        self.channel = grpc.aio.insecure_channel(address)
        self.stub = workforce_pb2_grpc.WorkforceServiceStub(self.channel)
        self.deadline_s = deadline_s

    async def get(self, tenant_id: str, employee_id: str) -> Colleague | None:
        tenant = common_pb2.TenantContext(tenant_id=tenant_id)
        try:
            emp = await self.stub.GetEmployee(
                workforce_pb2.GetEmployeeRequest(tenant=tenant, employee_id=employee_id),
                timeout=self.deadline_s,
            )
            got = await self.stub.GetVersion(
                workforce_pb2.GetVersionRequest(tenant=tenant, employee_id=employee_id),
                timeout=self.deadline_s,
            )
        except grpc.aio.AioRpcError as exc:
            if exc.code() in (grpc.StatusCode.NOT_FOUND, grpc.StatusCode.INVALID_ARGUMENT):
                return None
            raise
        e, v = emp.employee, got.version
        return Colleague(
            employee_id=employee_id,
            name=e.name,
            title=e.title,
            role=v.role,
            instructions=v.instructions,
            model=v.model,
            status="paused" if e.status == workforce_pb2.EMPLOYEE_STATUS_PAUSED else "active",
        )
