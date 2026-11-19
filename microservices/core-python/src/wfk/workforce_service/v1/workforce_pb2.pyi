import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from wfk.common.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EmployeeStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EMPLOYEE_STATUS_UNSPECIFIED: _ClassVar[EmployeeStatus]
    EMPLOYEE_STATUS_ACTIVE: _ClassVar[EmployeeStatus]
    EMPLOYEE_STATUS_PAUSED: _ClassVar[EmployeeStatus]

class RiskTier(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RISK_TIER_UNSPECIFIED: _ClassVar[RiskTier]
    RISK_TIER_R0: _ClassVar[RiskTier]
    RISK_TIER_R1: _ClassVar[RiskTier]
    RISK_TIER_R2: _ClassVar[RiskTier]
    RISK_TIER_R3: _ClassVar[RiskTier]

class HireRequestStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    HIRE_REQUEST_STATUS_UNSPECIFIED: _ClassVar[HireRequestStatus]
    HIRE_REQUEST_STATUS_PENDING: _ClassVar[HireRequestStatus]
    HIRE_REQUEST_STATUS_APPROVED: _ClassVar[HireRequestStatus]
    HIRE_REQUEST_STATUS_REJECTED: _ClassVar[HireRequestStatus]
EMPLOYEE_STATUS_UNSPECIFIED: EmployeeStatus
EMPLOYEE_STATUS_ACTIVE: EmployeeStatus
EMPLOYEE_STATUS_PAUSED: EmployeeStatus
RISK_TIER_UNSPECIFIED: RiskTier
RISK_TIER_R0: RiskTier
RISK_TIER_R1: RiskTier
RISK_TIER_R2: RiskTier
RISK_TIER_R3: RiskTier
HIRE_REQUEST_STATUS_UNSPECIFIED: HireRequestStatus
HIRE_REQUEST_STATUS_PENDING: HireRequestStatus
HIRE_REQUEST_STATUS_APPROVED: HireRequestStatus
HIRE_REQUEST_STATUS_REJECTED: HireRequestStatus

class ToolGrant(_message.Message):
    __slots__ = ("tool", "tier")
    TOOL_FIELD_NUMBER: _ClassVar[int]
    TIER_FIELD_NUMBER: _ClassVar[int]
    tool: str
    tier: RiskTier
    def __init__(self, tool: _Optional[str] = ..., tier: _Optional[_Union[RiskTier, str]] = ...) -> None: ...

class Limits(_message.Message):
    __slots__ = ("max_steps", "max_cost_usd")
    MAX_STEPS_FIELD_NUMBER: _ClassVar[int]
    MAX_COST_USD_FIELD_NUMBER: _ClassVar[int]
    max_steps: int
    max_cost_usd: str
    def __init__(self, max_steps: _Optional[int] = ..., max_cost_usd: _Optional[str] = ...) -> None: ...

class Employee(_message.Message):
    __slots__ = ("employee_id", "name", "status", "active_version", "created_at", "title", "department_id", "manager_id", "is_co_ceo")
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_VERSION_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    MANAGER_ID_FIELD_NUMBER: _ClassVar[int]
    IS_CO_CEO_FIELD_NUMBER: _ClassVar[int]
    employee_id: str
    name: str
    status: EmployeeStatus
    active_version: int
    created_at: _timestamp_pb2.Timestamp
    title: str
    department_id: str
    manager_id: str
    is_co_ceo: bool
    def __init__(self, employee_id: _Optional[str] = ..., name: _Optional[str] = ..., status: _Optional[_Union[EmployeeStatus, str]] = ..., active_version: _Optional[int] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., title: _Optional[str] = ..., department_id: _Optional[str] = ..., manager_id: _Optional[str] = ..., is_co_ceo: _Optional[bool] = ...) -> None: ...

class Department(_message.Message):
    __slots__ = ("department_id", "name", "head_employee_id", "created_at")
    DEPARTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    HEAD_EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    department_id: str
    name: str
    head_employee_id: str
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, department_id: _Optional[str] = ..., name: _Optional[str] = ..., head_employee_id: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class EmployeeVersion(_message.Message):
    __slots__ = ("employee_id", "version", "role", "instructions", "model", "tool_grants", "auto_approve", "limits", "created_by", "created_at")
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    TOOL_GRANTS_FIELD_NUMBER: _ClassVar[int]
    AUTO_APPROVE_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    CREATED_BY_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    employee_id: str
    version: int
    role: str
    instructions: str
    model: str
    tool_grants: _containers.RepeatedCompositeFieldContainer[ToolGrant]
    auto_approve: _containers.RepeatedScalarFieldContainer[str]
    limits: Limits
    created_by: str
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, employee_id: _Optional[str] = ..., version: _Optional[int] = ..., role: _Optional[str] = ..., instructions: _Optional[str] = ..., model: _Optional[str] = ..., tool_grants: _Optional[_Iterable[_Union[ToolGrant, _Mapping]]] = ..., auto_approve: _Optional[_Iterable[str]] = ..., limits: _Optional[_Union[Limits, _Mapping]] = ..., created_by: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class CreateEmployeeRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "name")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    name: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class CreateEmployeeResponse(_message.Message):
    __slots__ = ("employee",)
    EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    employee: Employee
    def __init__(self, employee: _Optional[_Union[Employee, _Mapping]] = ...) -> None: ...

class GetEmployeeRequest(_message.Message):
    __slots__ = ("tenant", "employee_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ...) -> None: ...

class GetEmployeeResponse(_message.Message):
    __slots__ = ("employee",)
    EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    employee: Employee
    def __init__(self, employee: _Optional[_Union[Employee, _Mapping]] = ...) -> None: ...

class ListEmployeesRequest(_message.Message):
    __slots__ = ("tenant",)
    TENANT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ...) -> None: ...

class ListEmployeesResponse(_message.Message):
    __slots__ = ("employees",)
    EMPLOYEES_FIELD_NUMBER: _ClassVar[int]
    employees: _containers.RepeatedCompositeFieldContainer[Employee]
    def __init__(self, employees: _Optional[_Iterable[_Union[Employee, _Mapping]]] = ...) -> None: ...

class PublishVersionRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "idempotency_key", "role", "instructions", "model", "tool_grants", "auto_approve", "limits")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    TOOL_GRANTS_FIELD_NUMBER: _ClassVar[int]
    AUTO_APPROVE_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    idempotency_key: _common_pb2.IdempotencyKey
    role: str
    instructions: str
    model: str
    tool_grants: _containers.RepeatedCompositeFieldContainer[ToolGrant]
    auto_approve: _containers.RepeatedScalarFieldContainer[str]
    limits: Limits
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., role: _Optional[str] = ..., instructions: _Optional[str] = ..., model: _Optional[str] = ..., tool_grants: _Optional[_Iterable[_Union[ToolGrant, _Mapping]]] = ..., auto_approve: _Optional[_Iterable[str]] = ..., limits: _Optional[_Union[Limits, _Mapping]] = ...) -> None: ...

class PublishVersionResponse(_message.Message):
    __slots__ = ("version",)
    VERSION_FIELD_NUMBER: _ClassVar[int]
    version: EmployeeVersion
    def __init__(self, version: _Optional[_Union[EmployeeVersion, _Mapping]] = ...) -> None: ...

class SetActiveVersionRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "version")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    version: int
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., version: _Optional[int] = ...) -> None: ...

class SetActiveVersionResponse(_message.Message):
    __slots__ = ("employee",)
    EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    employee: Employee
    def __init__(self, employee: _Optional[_Union[Employee, _Mapping]] = ...) -> None: ...

class GetVersionRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "version")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    version: int
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., version: _Optional[int] = ...) -> None: ...

class GetVersionResponse(_message.Message):
    __slots__ = ("version", "status")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    version: EmployeeVersion
    status: EmployeeStatus
    def __init__(self, version: _Optional[_Union[EmployeeVersion, _Mapping]] = ..., status: _Optional[_Union[EmployeeStatus, str]] = ...) -> None: ...

class PauseEmployeeRequest(_message.Message):
    __slots__ = ("tenant", "employee_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ...) -> None: ...

class PauseEmployeeResponse(_message.Message):
    __slots__ = ("employee",)
    EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    employee: Employee
    def __init__(self, employee: _Optional[_Union[Employee, _Mapping]] = ...) -> None: ...

class ResumeEmployeeRequest(_message.Message):
    __slots__ = ("tenant", "employee_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ...) -> None: ...

class ResumeEmployeeResponse(_message.Message):
    __slots__ = ("employee",)
    EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    employee: Employee
    def __init__(self, employee: _Optional[_Union[Employee, _Mapping]] = ...) -> None: ...

class CreateDepartmentRequest(_message.Message):
    __slots__ = ("tenant", "department_id", "name")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    department_id: str
    name: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., department_id: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class CreateDepartmentResponse(_message.Message):
    __slots__ = ("department",)
    DEPARTMENT_FIELD_NUMBER: _ClassVar[int]
    department: Department
    def __init__(self, department: _Optional[_Union[Department, _Mapping]] = ...) -> None: ...

class ListDepartmentsRequest(_message.Message):
    __slots__ = ("tenant",)
    TENANT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ...) -> None: ...

class ListDepartmentsResponse(_message.Message):
    __slots__ = ("departments",)
    DEPARTMENTS_FIELD_NUMBER: _ClassVar[int]
    departments: _containers.RepeatedCompositeFieldContainer[Department]
    def __init__(self, departments: _Optional[_Iterable[_Union[Department, _Mapping]]] = ...) -> None: ...

class PlaceEmployeeRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "title", "department_id", "manager_id", "is_co_ceo", "department_head")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    MANAGER_ID_FIELD_NUMBER: _ClassVar[int]
    IS_CO_CEO_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_HEAD_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    title: str
    department_id: str
    manager_id: str
    is_co_ceo: bool
    department_head: bool
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., title: _Optional[str] = ..., department_id: _Optional[str] = ..., manager_id: _Optional[str] = ..., is_co_ceo: _Optional[bool] = ..., department_head: _Optional[bool] = ...) -> None: ...

class PlaceEmployeeResponse(_message.Message):
    __slots__ = ("employee",)
    EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    employee: Employee
    def __init__(self, employee: _Optional[_Union[Employee, _Mapping]] = ...) -> None: ...

class GetOrgChartRequest(_message.Message):
    __slots__ = ("tenant",)
    TENANT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ...) -> None: ...

class GetOrgChartResponse(_message.Message):
    __slots__ = ("employees", "departments")
    EMPLOYEES_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENTS_FIELD_NUMBER: _ClassVar[int]
    employees: _containers.RepeatedCompositeFieldContainer[Employee]
    departments: _containers.RepeatedCompositeFieldContainer[Department]
    def __init__(self, employees: _Optional[_Iterable[_Union[Employee, _Mapping]]] = ..., departments: _Optional[_Iterable[_Union[Department, _Mapping]]] = ...) -> None: ...

class Hire(_message.Message):
    __slots__ = ("employee_id", "name", "title", "department_id", "manager_id", "is_co_ceo", "department_head", "role", "instructions", "model", "max_cost_usd")
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    MANAGER_ID_FIELD_NUMBER: _ClassVar[int]
    IS_CO_CEO_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_HEAD_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    MAX_COST_USD_FIELD_NUMBER: _ClassVar[int]
    employee_id: str
    name: str
    title: str
    department_id: str
    manager_id: str
    is_co_ceo: bool
    department_head: bool
    role: str
    instructions: str
    model: str
    max_cost_usd: str
    def __init__(self, employee_id: _Optional[str] = ..., name: _Optional[str] = ..., title: _Optional[str] = ..., department_id: _Optional[str] = ..., manager_id: _Optional[str] = ..., is_co_ceo: _Optional[bool] = ..., department_head: _Optional[bool] = ..., role: _Optional[str] = ..., instructions: _Optional[str] = ..., model: _Optional[str] = ..., max_cost_usd: _Optional[str] = ...) -> None: ...

class HireRequest(_message.Message):
    __slots__ = ("request_id", "hire", "reason", "requested_by", "status", "decided_by", "decision_note", "created_at", "decided_at")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    HIRE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    REQUESTED_BY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DECIDED_BY_FIELD_NUMBER: _ClassVar[int]
    DECISION_NOTE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    DECIDED_AT_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    hire: Hire
    reason: str
    requested_by: str
    status: HireRequestStatus
    decided_by: str
    decision_note: str
    created_at: _timestamp_pb2.Timestamp
    decided_at: _timestamp_pb2.Timestamp
    def __init__(self, request_id: _Optional[str] = ..., hire: _Optional[_Union[Hire, _Mapping]] = ..., reason: _Optional[str] = ..., requested_by: _Optional[str] = ..., status: _Optional[_Union[HireRequestStatus, str]] = ..., decided_by: _Optional[str] = ..., decision_note: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., decided_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class HireEmployeeRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "hire")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    HIRE_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    hire: Hire
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., hire: _Optional[_Union[Hire, _Mapping]] = ...) -> None: ...

class HireEmployeeResponse(_message.Message):
    __slots__ = ("employee", "version")
    EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    employee: Employee
    version: EmployeeVersion
    def __init__(self, employee: _Optional[_Union[Employee, _Mapping]] = ..., version: _Optional[_Union[EmployeeVersion, _Mapping]] = ...) -> None: ...

class RequestHireRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "hire", "reason")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    HIRE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    hire: Hire
    reason: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., hire: _Optional[_Union[Hire, _Mapping]] = ..., reason: _Optional[str] = ...) -> None: ...

class RequestHireResponse(_message.Message):
    __slots__ = ("request",)
    REQUEST_FIELD_NUMBER: _ClassVar[int]
    request: HireRequest
    def __init__(self, request: _Optional[_Union[HireRequest, _Mapping]] = ...) -> None: ...

class ListHireRequestsRequest(_message.Message):
    __slots__ = ("tenant", "status")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    status: HireRequestStatus
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., status: _Optional[_Union[HireRequestStatus, str]] = ...) -> None: ...

class ListHireRequestsResponse(_message.Message):
    __slots__ = ("requests",)
    REQUESTS_FIELD_NUMBER: _ClassVar[int]
    requests: _containers.RepeatedCompositeFieldContainer[HireRequest]
    def __init__(self, requests: _Optional[_Iterable[_Union[HireRequest, _Mapping]]] = ...) -> None: ...

class DecideHireRequestRequest(_message.Message):
    __slots__ = ("tenant", "request_id", "approved", "note")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    APPROVED_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    request_id: str
    approved: bool
    note: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., request_id: _Optional[str] = ..., approved: _Optional[bool] = ..., note: _Optional[str] = ...) -> None: ...

class DecideHireRequestResponse(_message.Message):
    __slots__ = ("request", "employee")
    REQUEST_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    request: HireRequest
    employee: Employee
    def __init__(self, request: _Optional[_Union[HireRequest, _Mapping]] = ..., employee: _Optional[_Union[Employee, _Mapping]] = ...) -> None: ...

class CanDelegateRequest(_message.Message):
    __slots__ = ("tenant", "from_employee", "to_employee")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    FROM_EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    TO_EMPLOYEE_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    from_employee: str
    to_employee: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., from_employee: _Optional[str] = ..., to_employee: _Optional[str] = ...) -> None: ...

class CanDelegateResponse(_message.Message):
    __slots__ = ("allowed", "reason")
    ALLOWED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    allowed: bool
    reason: str
    def __init__(self, allowed: _Optional[bool] = ..., reason: _Optional[str] = ...) -> None: ...
