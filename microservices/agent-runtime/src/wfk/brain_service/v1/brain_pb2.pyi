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

class StepType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STEP_TYPE_UNSPECIFIED: _ClassVar[StepType]
    STEP_TYPE_PLANNING: _ClassVar[StepType]
    STEP_TYPE_ACTING: _ClassVar[StepType]
    STEP_TYPE_REPLYING: _ClassVar[StepType]
    STEP_TYPE_LIVE_MEETING: _ClassVar[StepType]
STEP_TYPE_UNSPECIFIED: StepType
STEP_TYPE_PLANNING: StepType
STEP_TYPE_ACTING: StepType
STEP_TYPE_REPLYING: StepType
STEP_TYPE_LIVE_MEETING: StepType

class RecallRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "run_id", "step", "query", "step_type", "token_budget", "as_of")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    STEP_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    STEP_TYPE_FIELD_NUMBER: _ClassVar[int]
    TOKEN_BUDGET_FIELD_NUMBER: _ClassVar[int]
    AS_OF_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    run_id: str
    step: int
    query: str
    step_type: StepType
    token_budget: int
    as_of: _timestamp_pb2.Timestamp
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., run_id: _Optional[str] = ..., step: _Optional[int] = ..., query: _Optional[str] = ..., step_type: _Optional[_Union[StepType, str]] = ..., token_budget: _Optional[int] = ..., as_of: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class RecalledItem(_message.Message):
    __slots__ = ("memory_id", "kind", "content", "score", "trust", "recorded_at", "source_ref")
    MEMORY_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    TRUST_FIELD_NUMBER: _ClassVar[int]
    RECORDED_AT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REF_FIELD_NUMBER: _ClassVar[int]
    memory_id: str
    kind: str
    content: str
    score: float
    trust: str
    recorded_at: _timestamp_pb2.Timestamp
    source_ref: str
    def __init__(self, memory_id: _Optional[str] = ..., kind: _Optional[str] = ..., content: _Optional[str] = ..., score: _Optional[float] = ..., trust: _Optional[str] = ..., recorded_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., source_ref: _Optional[str] = ...) -> None: ...

class RecallResponse(_message.Message):
    __slots__ = ("recall_id", "identity", "identity_version", "rules", "items", "tokens_used", "degraded", "skipped")
    RECALL_ID_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_VERSION_FIELD_NUMBER: _ClassVar[int]
    RULES_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    TOKENS_USED_FIELD_NUMBER: _ClassVar[int]
    DEGRADED_FIELD_NUMBER: _ClassVar[int]
    SKIPPED_FIELD_NUMBER: _ClassVar[int]
    recall_id: str
    identity: str
    identity_version: int
    rules: _containers.RepeatedScalarFieldContainer[str]
    items: _containers.RepeatedCompositeFieldContainer[RecalledItem]
    tokens_used: int
    degraded: bool
    skipped: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, recall_id: _Optional[str] = ..., identity: _Optional[str] = ..., identity_version: _Optional[int] = ..., rules: _Optional[_Iterable[str]] = ..., items: _Optional[_Iterable[_Union[RecalledItem, _Mapping]]] = ..., tokens_used: _Optional[int] = ..., degraded: _Optional[bool] = ..., skipped: _Optional[_Iterable[str]] = ...) -> None: ...
