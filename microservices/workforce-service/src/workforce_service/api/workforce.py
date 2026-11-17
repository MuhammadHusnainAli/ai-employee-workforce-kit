"""WorkforceService over gRPC (contracts/proto/wfk/workforce_service/v1/workforce.proto)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, NoReturn, TypeVar

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from psycopg.rows import DictRow
from pydantic import ValidationError

from wfk.common.v1 import common_pb2
from wfk.workforce_service.v1 import workforce_pb2 as pb
from wfk.workforce_service.v1 import workforce_pb2_grpc

from workforce_service.adapters.postgres.employees import Conflict, Employees, NotFound
from workforce_service.domain.employees import (
    Limits,
    Tier,
    ToolGrant,
    VersionSpec,
    valid_employee_id,
)

T = TypeVar("T")
TIER_TO_PB: dict[Tier, pb.RiskTier] = {
    "R0": pb.RISK_TIER_R0,
    "R1": pb.RISK_TIER_R1,
    "R2": pb.RISK_TIER_R2,
    "R3": pb.RISK_TIER_R3,
}
TIER_FROM_PB: dict[int, Tier] = {v: k for k, v in TIER_TO_PB.items()}
STATUS_TO_PB = {"active": pb.EMPLOYEE_STATUS_ACTIVE, "paused": pb.EMPLOYEE_STATUS_PAUSED}


class BadRequest(ValueError):
    pass


async def _abort(context: grpc.aio.ServicerContext, code: grpc.StatusCode, msg: str) -> NoReturn:
    await context.abort(code, msg)
    raise AssertionError("unreachable")


def _ts(value: Any) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(value)
    return ts


def _tenant(t: common_pb2.TenantContext) -> str:
    try:
        return str(uuid.UUID(t.tenant_id))
    except ValueError:
        raise BadRequest("tenant.tenant_id must be a UUID") from None


def _actor(t: common_pb2.TenantContext) -> str:
    return t.actor.id or "unknown"


def _employee_id(value: str) -> str:
    if not valid_employee_id(value):
        raise BadRequest("employee_id must be lowercase letters, digits and dashes, 2 to 63 long")
    return value


def employee_pb(row: DictRow) -> pb.Employee:
    return pb.Employee(
        employee_id=row["employee_id"],
        name=row["name"],
        status=STATUS_TO_PB[row["status"]],
        active_version=row["active_version"] or 0,
        created_at=_ts(row["created_at"]),
    )


def version_pb(row: DictRow) -> pb.EmployeeVersion:
    limits = row["limits"]
    return pb.EmployeeVersion(
        employee_id=row["employee_id"],
        version=row["version"],
        role=row["role"],
        instructions=row["instructions"],
        model=row["model"],
        tool_grants=[
            pb.ToolGrant(tool=g["tool"], tier=TIER_TO_PB[g["tier"]]) for g in row["tool_grants"]
        ],
        auto_approve=list(row["auto_approve"]),
        limits=pb.Limits(max_steps=limits["max_steps"], max_cost_usd=str(limits["max_cost_usd"])),
        created_by=row["created_by"],
        created_at=_ts(row["created_at"]),
    )


def spec_from(request: pb.PublishVersionRequest) -> VersionSpec:
    try:
        grants = [ToolGrant(tool=g.tool, tier=TIER_FROM_PB[g.tier]) for g in request.tool_grants]
    except KeyError:
        raise BadRequest("every tool grant needs a tier (R0 to R3)") from None
    limits: dict[str, Any] = {}
    if request.limits.max_steps:
        limits["max_steps"] = request.limits.max_steps
    if request.limits.max_cost_usd:
        limits["max_cost_usd"] = Decimal(request.limits.max_cost_usd)
    return VersionSpec(
        role=request.role,
        instructions=request.instructions,
        model=request.model,
        tool_grants=grants,
        auto_approve=list(request.auto_approve),
        limits=Limits(**limits),
    )


class WorkforceServicer(workforce_pb2_grpc.WorkforceServiceServicer):
    def __init__(self, employees: Employees) -> None:
        self.employees = employees

    async def _guard(
        self, context: grpc.aio.ServicerContext, call: Callable[[], Awaitable[T]]
    ) -> T:
        """Map domain errors to gRPC status codes."""
        try:
            return await call()
        except (BadRequest, ValidationError, ArithmeticError) as exc:
            await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except NotFound as exc:
            await _abort(context, grpc.StatusCode.NOT_FOUND, str(exc))
        except Conflict as exc:
            await _abort(context, grpc.StatusCode.ALREADY_EXISTS, str(exc))

    async def CreateEmployee(
        self, request: pb.CreateEmployeeRequest, context: grpc.aio.ServicerContext
    ) -> pb.CreateEmployeeResponse:
        async def call() -> pb.CreateEmployeeResponse:
            if not request.name.strip():
                raise BadRequest("name is required")
            row = await self.employees.create(
                _tenant(request.tenant), _employee_id(request.employee_id), request.name,
                by=_actor(request.tenant),
            )  # fmt: skip
            return pb.CreateEmployeeResponse(employee=employee_pb(row))

        return await self._guard(context, call)

    async def GetEmployee(
        self, request: pb.GetEmployeeRequest, context: grpc.aio.ServicerContext
    ) -> pb.GetEmployeeResponse:
        async def call() -> pb.GetEmployeeResponse:
            row = await self.employees.get(_tenant(request.tenant), request.employee_id)
            return pb.GetEmployeeResponse(employee=employee_pb(row))

        return await self._guard(context, call)

    async def ListEmployees(
        self, request: pb.ListEmployeesRequest, context: grpc.aio.ServicerContext
    ) -> pb.ListEmployeesResponse:
        async def call() -> pb.ListEmployeesResponse:
            rows = await self.employees.list(_tenant(request.tenant))
            return pb.ListEmployeesResponse(employees=[employee_pb(r) for r in rows])

        return await self._guard(context, call)

    async def PublishVersion(
        self, request: pb.PublishVersionRequest, context: grpc.aio.ServicerContext
    ) -> pb.PublishVersionResponse:
        async def call() -> pb.PublishVersionResponse:
            if not request.idempotency_key.value:
                raise BadRequest("idempotency_key is required")
            row = await self.employees.publish(
                _tenant(request.tenant), _employee_id(request.employee_id),
                request.idempotency_key.value, spec_from(request), by=_actor(request.tenant),
            )  # fmt: skip
            return pb.PublishVersionResponse(version=version_pb(row))

        return await self._guard(context, call)

    async def SetActiveVersion(
        self, request: pb.SetActiveVersionRequest, context: grpc.aio.ServicerContext
    ) -> pb.SetActiveVersionResponse:
        async def call() -> pb.SetActiveVersionResponse:
            if request.version < 1:
                raise BadRequest("version must be 1 or more")
            row = await self.employees.set_active(
                _tenant(request.tenant), request.employee_id, request.version
            )
            return pb.SetActiveVersionResponse(employee=employee_pb(row))

        return await self._guard(context, call)

    async def GetVersion(
        self, request: pb.GetVersionRequest, context: grpc.aio.ServicerContext
    ) -> pb.GetVersionResponse:
        async def call() -> pb.GetVersionResponse:
            row, status = await self.employees.version(
                _tenant(request.tenant), request.employee_id, request.version
            )
            return pb.GetVersionResponse(version=version_pb(row), status=STATUS_TO_PB[status])

        return await self._guard(context, call)

    async def PauseEmployee(
        self, request: pb.PauseEmployeeRequest, context: grpc.aio.ServicerContext
    ) -> pb.PauseEmployeeResponse:
        async def call() -> pb.PauseEmployeeResponse:
            row = await self.employees.set_status(
                _tenant(request.tenant), request.employee_id, "paused"
            )
            return pb.PauseEmployeeResponse(employee=employee_pb(row))

        return await self._guard(context, call)

    async def ResumeEmployee(
        self, request: pb.ResumeEmployeeRequest, context: grpc.aio.ServicerContext
    ) -> pb.ResumeEmployeeResponse:
        async def call() -> pb.ResumeEmployeeResponse:
            row = await self.employees.set_status(
                _tenant(request.tenant), request.employee_id, "active"
            )
            return pb.ResumeEmployeeResponse(employee=employee_pb(row))

        return await self._guard(context, call)
