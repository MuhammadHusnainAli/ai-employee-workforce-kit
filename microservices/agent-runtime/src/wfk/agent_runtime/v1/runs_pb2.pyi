import datetime

from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from wfk.common.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RunStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RUN_STATUS_UNSPECIFIED: _ClassVar[RunStatus]
    RUN_STATUS_RUNNING: _ClassVar[RunStatus]
    RUN_STATUS_WAITING_HUMAN: _ClassVar[RunStatus]
    RUN_STATUS_COMPLETED: _ClassVar[RunStatus]
    RUN_STATUS_FAILED: _ClassVar[RunStatus]
RUN_STATUS_UNSPECIFIED: RunStatus
RUN_STATUS_RUNNING: RunStatus
RUN_STATUS_WAITING_HUMAN: RunStatus
RUN_STATUS_COMPLETED: RunStatus
RUN_STATUS_FAILED: RunStatus

class Run(_message.Message):
    __slots__ = ("run_id", "tenant_id", "employee_id", "task", "status", "stop_reason", "output", "steps", "input_tokens", "output_tokens", "cost_usd", "created_at", "updated_at")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    STOP_REASON_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    STEPS_FIELD_NUMBER: _ClassVar[int]
    INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    COST_USD_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    tenant_id: str
    employee_id: str
    task: str
    status: RunStatus
    stop_reason: str
    output: str
    steps: int
    input_tokens: int
    output_tokens: int
    cost_usd: str
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    def __init__(self, run_id: _Optional[str] = ..., tenant_id: _Optional[str] = ..., employee_id: _Optional[str] = ..., task: _Optional[str] = ..., status: _Optional[_Union[RunStatus, str]] = ..., stop_reason: _Optional[str] = ..., output: _Optional[str] = ..., steps: _Optional[int] = ..., input_tokens: _Optional[int] = ..., output_tokens: _Optional[int] = ..., cost_usd: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class Step(_message.Message):
    __slots__ = ("seq", "step", "kind", "status_after", "model", "prompt_hash", "input_tokens", "output_tokens", "cost_usd", "decision", "results", "created_at")
    SEQ_FIELD_NUMBER: _ClassVar[int]
    STEP_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    STATUS_AFTER_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    PROMPT_HASH_FIELD_NUMBER: _ClassVar[int]
    INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    COST_USD_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    seq: int
    step: int
    kind: str
    status_after: RunStatus
    model: str
    prompt_hash: str
    input_tokens: int
    output_tokens: int
    cost_usd: str
    decision: _struct_pb2.Struct
    results: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Struct]
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, seq: _Optional[int] = ..., step: _Optional[int] = ..., kind: _Optional[str] = ..., status_after: _Optional[_Union[RunStatus, str]] = ..., model: _Optional[str] = ..., prompt_hash: _Optional[str] = ..., input_tokens: _Optional[int] = ..., output_tokens: _Optional[int] = ..., cost_usd: _Optional[str] = ..., decision: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., results: _Optional[_Iterable[_Union[_struct_pb2.Struct, _Mapping]]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class GetRunRequest(_message.Message):
    __slots__ = ("tenant", "run_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    run_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., run_id: _Optional[str] = ...) -> None: ...

class GetRunResponse(_message.Message):
    __slots__ = ("run",)
    RUN_FIELD_NUMBER: _ClassVar[int]
    run: Run
    def __init__(self, run: _Optional[_Union[Run, _Mapping]] = ...) -> None: ...

class ListStepsRequest(_message.Message):
    __slots__ = ("tenant", "run_id", "page")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    run_id: str
    page: _common_pb2.PageRequest
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., run_id: _Optional[str] = ..., page: _Optional[_Union[_common_pb2.PageRequest, _Mapping]] = ...) -> None: ...

class ListStepsResponse(_message.Message):
    __slots__ = ("steps", "page")
    STEPS_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    steps: _containers.RepeatedCompositeFieldContainer[Step]
    page: _common_pb2.PageResponse
    def __init__(self, steps: _Optional[_Iterable[_Union[Step, _Mapping]]] = ..., page: _Optional[_Union[_common_pb2.PageResponse, _Mapping]] = ...) -> None: ...
