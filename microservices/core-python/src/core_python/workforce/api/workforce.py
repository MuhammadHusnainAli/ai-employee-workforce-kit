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

from core_python.workforce.adapters.postgres.employees import (
    Conflict,
    Employees,
    NotFound,
    Precondition,
)
from core_python.workforce.adapters.postgres.hiring import Hiring
from core_python.workforce.adapters.postgres.org import Org
from core_python.workforce.domain.employees import (
    Limits,
    Tier,
    ToolGrant,
    VersionSpec,
    valid_employee_id,
)
from core_python.workforce.domain.hiring import Hire
from core_python.workforce.domain.org import (
    InvalidPlacement,
    delegation_refusal,
    valid_department_id,
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


HIRE_STATUS_TO_PB = {
    "pending": pb.HIRE_REQUEST_STATUS_PENDING,
    "approved": pb.HIRE_REQUEST_STATUS_APPROVED,
    "rejected": pb.HIRE_REQUEST_STATUS_REJECTED,
}
HIRE_STATUS_FROM_PB = {v: k for k, v in HIRE_STATUS_TO_PB.items()}
ACTOR_KINDS = {
    common_pb2.ACTOR_KIND_HUMAN: "person",
    common_pb2.ACTOR_KIND_EMPLOYEE: "employee",
    common_pb2.ACTOR_KIND_SERVICE: "service",
}


class BadRequest(ValueError):
    pass


class Forbidden(Exception):
    pass


def _person_only(t: common_pb2.TenantContext) -> None:
    """A hire costs money: only a person hires or decides."""
    if t.actor.kind != common_pb2.ACTOR_KIND_HUMAN:
        raise Forbidden("only a person can hire or decide a hire request")


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
        title=row.get("title") or "",
        department_id=row.get("department_id") or "",
        manager_id=row.get("manager_id") or "",
        is_co_ceo=bool(row.get("is_co_ceo")),
    )


def department_pb(row: DictRow) -> pb.Department:
    return pb.Department(
        department_id=row["department_id"],
        name=row["name"],
        head_employee_id=row["head_employee_id"] or "",
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


def hire_from(h: pb.Hire) -> Hire:
    fields: dict[str, Any] = {
        "employee_id": _employee_id(h.employee_id),
        "name": h.name.strip(),
        "title": h.title.strip(),
        "department_id": h.department_id or None,
        "manager_id": h.manager_id or None,
        "is_co_ceo": h.is_co_ceo,
        "department_head": h.department_head,
        "role": h.role,
        "instructions": h.instructions,
        "model": h.model,
    }
    if h.max_cost_usd:
        fields["max_cost_usd"] = Decimal(h.max_cost_usd)
    return Hire(**fields)


def hire_pb(h: dict[str, Any]) -> pb.Hire:
    return pb.Hire(
        employee_id=h["employee_id"],
        name=h["name"],
        title=h.get("title") or "",
        department_id=h.get("department_id") or "",
        manager_id=h.get("manager_id") or "",
        is_co_ceo=bool(h.get("is_co_ceo")),
        department_head=bool(h.get("department_head")),
        role=h["role"],
        instructions=h.get("instructions") or "",
        model=h["model"],
        max_cost_usd=str(h.get("max_cost_usd") or ""),
    )


def hire_request_pb(row: DictRow) -> pb.HireRequest:
    req = pb.HireRequest(
        request_id=str(row["request_id"]),
        hire=hire_pb(row["hire"]),
        reason=row["reason"],
        requested_by=row["requested_by"],
        status=HIRE_STATUS_TO_PB[row["status"]],
        decided_by=row["decided_by"] or "",
        decision_note=row["decision_note"],
        created_at=_ts(row["created_at"]),
    )
    if row["decided_at"] is not None:
        req.decided_at.CopyFrom(_ts(row["decided_at"]))
    return req


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
    def __init__(self, employees: Employees, org: Org, hiring: Hiring) -> None:
        self.employees = employees
        self.org = org
        self.hiring = hiring

    async def _guard(
        self, context: grpc.aio.ServicerContext, call: Callable[[], Awaitable[T]]
    ) -> T:
        """Map domain errors to gRPC status codes."""
        try:
            return await call()
        except (BadRequest, ValidationError, ArithmeticError, InvalidPlacement) as exc:
            await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except NotFound as exc:
            await _abort(context, grpc.StatusCode.NOT_FOUND, str(exc))
        except Conflict as exc:
            await _abort(context, grpc.StatusCode.ALREADY_EXISTS, str(exc))
        except Precondition as exc:
            await _abort(context, grpc.StatusCode.FAILED_PRECONDITION, str(exc))
        except Forbidden as exc:
            await _abort(context, grpc.StatusCode.PERMISSION_DENIED, str(exc))

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

    # ---- the org chart -------------------------------------------------------------------

    async def CreateDepartment(
        self, request: pb.CreateDepartmentRequest, context: grpc.aio.ServicerContext
    ) -> pb.CreateDepartmentResponse:
        async def call() -> pb.CreateDepartmentResponse:
            if not valid_department_id(request.department_id):
                raise BadRequest(
                    "department_id must be lowercase letters, digits and dashes, 2 to 63 long"
                )
            if not request.name.strip():
                raise BadRequest("name is required")
            row = await self.org.create_department(
                _tenant(request.tenant), request.department_id, request.name.strip(),
                by=_actor(request.tenant),
            )  # fmt: skip
            return pb.CreateDepartmentResponse(department=department_pb(row))

        return await self._guard(context, call)

    async def ListDepartments(
        self, request: pb.ListDepartmentsRequest, context: grpc.aio.ServicerContext
    ) -> pb.ListDepartmentsResponse:
        async def call() -> pb.ListDepartmentsResponse:
            rows = await self.org.departments(_tenant(request.tenant))
            return pb.ListDepartmentsResponse(departments=[department_pb(r) for r in rows])

        return await self._guard(context, call)

    async def PlaceEmployee(
        self, request: pb.PlaceEmployeeRequest, context: grpc.aio.ServicerContext
    ) -> pb.PlaceEmployeeResponse:
        async def call() -> pb.PlaceEmployeeResponse:
            if len(request.title) > 200:
                raise BadRequest("title is at most 200 characters")
            row = await self.org.place(
                _tenant(request.tenant), request.employee_id, title=request.title.strip(),
                department_id=request.department_id or None,
                manager_id=request.manager_id or None, is_co_ceo=request.is_co_ceo,
                department_head=request.department_head,
            )  # fmt: skip
            return pb.PlaceEmployeeResponse(employee=employee_pb(row))

        return await self._guard(context, call)

    async def GetOrgChart(
        self, request: pb.GetOrgChartRequest, context: grpc.aio.ServicerContext
    ) -> pb.GetOrgChartResponse:
        async def call() -> pb.GetOrgChartResponse:
            people, depts = await self.org.chart(_tenant(request.tenant))
            return pb.GetOrgChartResponse(
                employees=[employee_pb(r) for r in people],
                departments=[department_pb(r) for r in depts],
            )

        return await self._guard(context, call)

    async def CanDelegate(
        self, request: pb.CanDelegateRequest, context: grpc.aio.ServicerContext
    ) -> pb.CanDelegateResponse:
        async def call() -> pb.CanDelegateResponse:
            people = await self.org.members(_tenant(request.tenant))
            why = delegation_refusal(people, request.from_employee, request.to_employee)
            return pb.CanDelegateResponse(allowed=why is None, reason=why or "")

        return await self._guard(context, call)

    # ---- hiring ----------------------------------------------------------------------------

    async def HireEmployee(
        self, request: pb.HireEmployeeRequest, context: grpc.aio.ServicerContext
    ) -> pb.HireEmployeeResponse:
        async def call() -> pb.HireEmployeeResponse:
            _person_only(request.tenant)
            if not request.idempotency_key.value:
                raise BadRequest("idempotency_key is required")
            employee, version = await self.hiring.hire(
                _tenant(request.tenant),
                request.idempotency_key.value,
                hire_from(request.hire),
                by=_actor(request.tenant),
            )
            return pb.HireEmployeeResponse(
                employee=employee_pb(employee), version=version_pb(version)
            )

        return await self._guard(context, call)

    async def RequestHire(
        self, request: pb.RequestHireRequest, context: grpc.aio.ServicerContext
    ) -> pb.RequestHireResponse:
        async def call() -> pb.RequestHireResponse:
            if not request.idempotency_key.value:
                raise BadRequest("idempotency_key is required")
            if len(request.reason) > 5_000:
                raise BadRequest("reason is at most 5,000 characters")
            row = await self.hiring.request(
                _tenant(request.tenant),
                request.idempotency_key.value,
                hire_from(request.hire),
                request.reason.strip(),
                by=_actor(request.tenant),
                by_kind=ACTOR_KINDS.get(request.tenant.actor.kind, "service"),
            )
            return pb.RequestHireResponse(request=hire_request_pb(row))

        return await self._guard(context, call)

    async def ListHireRequests(
        self, request: pb.ListHireRequestsRequest, context: grpc.aio.ServicerContext
    ) -> pb.ListHireRequestsResponse:
        async def call() -> pb.ListHireRequestsResponse:
            rows = await self.hiring.list(
                _tenant(request.tenant), HIRE_STATUS_FROM_PB.get(request.status)
            )
            return pb.ListHireRequestsResponse(requests=[hire_request_pb(r) for r in rows])

        return await self._guard(context, call)

    async def DecideHireRequest(
        self, request: pb.DecideHireRequestRequest, context: grpc.aio.ServicerContext
    ) -> pb.DecideHireRequestResponse:
        async def call() -> pb.DecideHireRequestResponse:
            _person_only(request.tenant)
            try:
                request_id = str(uuid.UUID(request.request_id))
            except ValueError:
                raise BadRequest("request_id must be a UUID") from None
            row, employee = await self.hiring.decide(
                _tenant(request.tenant),
                request_id,
                request.approved,
                request.note.strip(),
                by=_actor(request.tenant),
            )
            response = pb.DecideHireRequestResponse(request=hire_request_pb(row))
            if employee is not None:
                response.employee.CopyFrom(employee_pb(employee))
            return response

        return await self._guard(context, call)
