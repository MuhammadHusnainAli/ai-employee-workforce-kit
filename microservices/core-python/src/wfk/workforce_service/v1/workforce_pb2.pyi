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
EMPLOYEE_STATUS_UNSPECIFIED: EmployeeStatus
EMPLOYEE_STATUS_ACTIVE: EmployeeStatus
EMPLOYEE_STATUS_PAUSED: EmployeeStatus
RISK_TIER_UNSPECIFIED: RiskTier
RISK_TIER_R0: RiskTier
RISK_TIER_R1: RiskTier
RISK_TIER_R2: RiskTier
RISK_TIER_R3: RiskTier

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
    __slots__ = ("employee_id", "name", "status", "active_version", "created_at")
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_VERSION_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    employee_id: str
    name: str
    status: EmployeeStatus
    active_version: int
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, employee_id: _Optional[str] = ..., name: _Optional[str] = ..., status: _Optional[_Union[EmployeeStatus, str]] = ..., active_version: _Optional[int] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

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
