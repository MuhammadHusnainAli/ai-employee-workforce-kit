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

class TaskStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TASK_STATUS_UNSPECIFIED: _ClassVar[TaskStatus]
    TASK_STATUS_QUEUED: _ClassVar[TaskStatus]
    TASK_STATUS_RUNNING: _ClassVar[TaskStatus]
    TASK_STATUS_WAITING_HUMAN: _ClassVar[TaskStatus]
    TASK_STATUS_COMPLETED: _ClassVar[TaskStatus]
    TASK_STATUS_FAILED: _ClassVar[TaskStatus]
    TASK_STATUS_CANCELLED: _ClassVar[TaskStatus]
TASK_STATUS_UNSPECIFIED: TaskStatus
TASK_STATUS_QUEUED: TaskStatus
TASK_STATUS_RUNNING: TaskStatus
TASK_STATUS_WAITING_HUMAN: TaskStatus
TASK_STATUS_COMPLETED: TaskStatus
TASK_STATUS_FAILED: TaskStatus
TASK_STATUS_CANCELLED: TaskStatus

class Task(_message.Message):
    __slots__ = ("task_id", "employee_id", "title", "input", "priority", "status", "stop_reason", "created_by", "created_at", "updated_at", "parent_task_id", "root_task_id", "depth", "output", "delegated_by")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    STOP_REASON_FIELD_NUMBER: _ClassVar[int]
    CREATED_BY_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    PARENT_TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ROOT_TASK_ID_FIELD_NUMBER: _ClassVar[int]
    DEPTH_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    DELEGATED_BY_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    employee_id: str
    title: str
    input: str
    priority: int
    status: TaskStatus
    stop_reason: str
    created_by: str
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    parent_task_id: str
    root_task_id: str
    depth: int
    output: str
    delegated_by: str
    def __init__(self, task_id: _Optional[str] = ..., employee_id: _Optional[str] = ..., title: _Optional[str] = ..., input: _Optional[str] = ..., priority: _Optional[int] = ..., status: _Optional[_Union[TaskStatus, str]] = ..., stop_reason: _Optional[str] = ..., created_by: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., parent_task_id: _Optional[str] = ..., root_task_id: _Optional[str] = ..., depth: _Optional[int] = ..., output: _Optional[str] = ..., delegated_by: _Optional[str] = ...) -> None: ...

class CreateTaskRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "employee_id", "title", "input", "priority", "parent_task_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_FIELD_NUMBER: _ClassVar[int]
    PARENT_TASK_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    employee_id: str
    title: str
    input: str
    priority: int
    parent_task_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., employee_id: _Optional[str] = ..., title: _Optional[str] = ..., input: _Optional[str] = ..., priority: _Optional[int] = ..., parent_task_id: _Optional[str] = ...) -> None: ...

class CreateTaskResponse(_message.Message):
    __slots__ = ("task",)
    TASK_FIELD_NUMBER: _ClassVar[int]
    task: Task
    def __init__(self, task: _Optional[_Union[Task, _Mapping]] = ...) -> None: ...

class GetTaskRequest(_message.Message):
    __slots__ = ("tenant", "task_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    task_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., task_id: _Optional[str] = ...) -> None: ...

class GetTaskResponse(_message.Message):
    __slots__ = ("task",)
    TASK_FIELD_NUMBER: _ClassVar[int]
    task: Task
    def __init__(self, task: _Optional[_Union[Task, _Mapping]] = ...) -> None: ...

class ListTasksRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "status", "limit", "parent_task_id", "root_task_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    PARENT_TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ROOT_TASK_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    status: TaskStatus
    limit: int
    parent_task_id: str
    root_task_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., status: _Optional[_Union[TaskStatus, str]] = ..., limit: _Optional[int] = ..., parent_task_id: _Optional[str] = ..., root_task_id: _Optional[str] = ...) -> None: ...

class ListTasksResponse(_message.Message):
    __slots__ = ("tasks",)
    TASKS_FIELD_NUMBER: _ClassVar[int]
    tasks: _containers.RepeatedCompositeFieldContainer[Task]
    def __init__(self, tasks: _Optional[_Iterable[_Union[Task, _Mapping]]] = ...) -> None: ...

class CancelTaskRequest(_message.Message):
    __slots__ = ("tenant", "task_id", "reason")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    task_id: str
    reason: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., task_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class CancelTaskResponse(_message.Message):
    __slots__ = ("task",)
    TASK_FIELD_NUMBER: _ClassVar[int]
    task: Task
    def __init__(self, task: _Optional[_Union[Task, _Mapping]] = ...) -> None: ...

class DecideApprovalRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "task_id", "approved", "note")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    APPROVED_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    task_id: str
    approved: bool
    note: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., task_id: _Optional[str] = ..., approved: _Optional[bool] = ..., note: _Optional[str] = ...) -> None: ...

class DecideApprovalResponse(_message.Message):
    __slots__ = ("task",)
    TASK_FIELD_NUMBER: _ClassVar[int]
    task: Task
    def __init__(self, task: _Optional[_Union[Task, _Mapping]] = ...) -> None: ...

class ReplyToTaskRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "task_id", "text")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    task_id: str
    text: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., task_id: _Optional[str] = ..., text: _Optional[str] = ...) -> None: ...

class ReplyToTaskResponse(_message.Message):
    __slots__ = ("task",)
    TASK_FIELD_NUMBER: _ClassVar[int]
    task: Task
    def __init__(self, task: _Optional[_Union[Task, _Mapping]] = ...) -> None: ...
