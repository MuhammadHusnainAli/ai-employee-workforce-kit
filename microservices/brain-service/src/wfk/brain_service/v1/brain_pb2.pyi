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

class MemoryStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MEMORY_STATUS_UNSPECIFIED: _ClassVar[MemoryStatus]
    MEMORY_STATUS_ACTIVE: _ClassVar[MemoryStatus]
    MEMORY_STATUS_QUARANTINED: _ClassVar[MemoryStatus]
    MEMORY_STATUS_REJECTED: _ClassVar[MemoryStatus]

class FeedbackKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    FEEDBACK_KIND_UNSPECIFIED: _ClassVar[FeedbackKind]
    FEEDBACK_KIND_CORRECTION: _ClassVar[FeedbackKind]
    FEEDBACK_KIND_RATING: _ClassVar[FeedbackKind]
    FEEDBACK_KIND_APPROVAL_REJECTED: _ClassVar[FeedbackKind]
    FEEDBACK_KIND_COMMENT: _ClassVar[FeedbackKind]
STEP_TYPE_UNSPECIFIED: StepType
STEP_TYPE_PLANNING: StepType
STEP_TYPE_ACTING: StepType
STEP_TYPE_REPLYING: StepType
STEP_TYPE_LIVE_MEETING: StepType
MEMORY_STATUS_UNSPECIFIED: MemoryStatus
MEMORY_STATUS_ACTIVE: MemoryStatus
MEMORY_STATUS_QUARANTINED: MemoryStatus
MEMORY_STATUS_REJECTED: MemoryStatus
FEEDBACK_KIND_UNSPECIFIED: FeedbackKind
FEEDBACK_KIND_CORRECTION: FeedbackKind
FEEDBACK_KIND_RATING: FeedbackKind
FEEDBACK_KIND_APPROVAL_REJECTED: FeedbackKind
FEEDBACK_KIND_COMMENT: FeedbackKind

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

class InspectedMemory(_message.Message):
    __slots__ = ("memory_id", "employee_id", "kind", "content", "status", "trust", "confidence", "importance", "fact_key", "source_ref", "valid_from", "valid_to", "recorded_at", "superseded_by", "findings", "contradicts", "reinforced", "use_count")
    class FindingsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    MEMORY_ID_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    TRUST_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    IMPORTANCE_FIELD_NUMBER: _ClassVar[int]
    FACT_KEY_FIELD_NUMBER: _ClassVar[int]
    SOURCE_REF_FIELD_NUMBER: _ClassVar[int]
    VALID_FROM_FIELD_NUMBER: _ClassVar[int]
    VALID_TO_FIELD_NUMBER: _ClassVar[int]
    RECORDED_AT_FIELD_NUMBER: _ClassVar[int]
    SUPERSEDED_BY_FIELD_NUMBER: _ClassVar[int]
    FINDINGS_FIELD_NUMBER: _ClassVar[int]
    CONTRADICTS_FIELD_NUMBER: _ClassVar[int]
    REINFORCED_FIELD_NUMBER: _ClassVar[int]
    USE_COUNT_FIELD_NUMBER: _ClassVar[int]
    memory_id: str
    employee_id: str
    kind: str
    content: str
    status: MemoryStatus
    trust: str
    confidence: float
    importance: float
    fact_key: str
    source_ref: str
    valid_from: _timestamp_pb2.Timestamp
    valid_to: _timestamp_pb2.Timestamp
    recorded_at: _timestamp_pb2.Timestamp
    superseded_by: str
    findings: _containers.ScalarMap[str, int]
    contradicts: str
    reinforced: int
    use_count: int
    def __init__(self, memory_id: _Optional[str] = ..., employee_id: _Optional[str] = ..., kind: _Optional[str] = ..., content: _Optional[str] = ..., status: _Optional[_Union[MemoryStatus, str]] = ..., trust: _Optional[str] = ..., confidence: _Optional[float] = ..., importance: _Optional[float] = ..., fact_key: _Optional[str] = ..., source_ref: _Optional[str] = ..., valid_from: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., valid_to: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., recorded_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., superseded_by: _Optional[str] = ..., findings: _Optional[_Mapping[str, int]] = ..., contradicts: _Optional[str] = ..., reinforced: _Optional[int] = ..., use_count: _Optional[int] = ...) -> None: ...

class ListMemoriesRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "kinds", "statuses", "include_history", "page")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    KINDS_FIELD_NUMBER: _ClassVar[int]
    STATUSES_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_HISTORY_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    kinds: _containers.RepeatedScalarFieldContainer[str]
    statuses: _containers.RepeatedScalarFieldContainer[MemoryStatus]
    include_history: bool
    page: _common_pb2.PageRequest
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., kinds: _Optional[_Iterable[str]] = ..., statuses: _Optional[_Iterable[_Union[MemoryStatus, str]]] = ..., include_history: _Optional[bool] = ..., page: _Optional[_Union[_common_pb2.PageRequest, _Mapping]] = ...) -> None: ...

class ListMemoriesResponse(_message.Message):
    __slots__ = ("memories", "page")
    MEMORIES_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    memories: _containers.RepeatedCompositeFieldContainer[InspectedMemory]
    page: _common_pb2.PageResponse
    def __init__(self, memories: _Optional[_Iterable[_Union[InspectedMemory, _Mapping]]] = ..., page: _Optional[_Union[_common_pb2.PageResponse, _Mapping]] = ...) -> None: ...

class GetMemoryRequest(_message.Message):
    __slots__ = ("tenant", "memory_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    MEMORY_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    memory_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., memory_id: _Optional[str] = ...) -> None: ...

class Episode(_message.Message):
    __slots__ = ("run_id", "task", "outcome", "ended_at")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    ENDED_AT_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    task: str
    outcome: str
    ended_at: _timestamp_pb2.Timestamp
    def __init__(self, run_id: _Optional[str] = ..., task: _Optional[str] = ..., outcome: _Optional[str] = ..., ended_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class GetMemoryResponse(_message.Message):
    __slots__ = ("memory", "replaced", "replaced_by", "episode")
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    REPLACED_FIELD_NUMBER: _ClassVar[int]
    REPLACED_BY_FIELD_NUMBER: _ClassVar[int]
    EPISODE_FIELD_NUMBER: _ClassVar[int]
    memory: InspectedMemory
    replaced: _containers.RepeatedCompositeFieldContainer[InspectedMemory]
    replaced_by: InspectedMemory
    episode: Episode
    def __init__(self, memory: _Optional[_Union[InspectedMemory, _Mapping]] = ..., replaced: _Optional[_Iterable[_Union[InspectedMemory, _Mapping]]] = ..., replaced_by: _Optional[_Union[InspectedMemory, _Mapping]] = ..., episode: _Optional[_Union[Episode, _Mapping]] = ...) -> None: ...

class CorrectMemoryRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "memory_id", "content")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    MEMORY_ID_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    memory_id: str
    content: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., memory_id: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class CorrectMemoryResponse(_message.Message):
    __slots__ = ("memory",)
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    memory: InspectedMemory
    def __init__(self, memory: _Optional[_Union[InspectedMemory, _Mapping]] = ...) -> None: ...

class DeleteMemoryRequest(_message.Message):
    __slots__ = ("tenant", "memory_id", "reason")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    MEMORY_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    memory_id: str
    reason: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., memory_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class DeleteMemoryResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ReviewMemoryRequest(_message.Message):
    __slots__ = ("tenant", "memory_id", "approve", "note")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    MEMORY_ID_FIELD_NUMBER: _ClassVar[int]
    APPROVE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    memory_id: str
    approve: bool
    note: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., memory_id: _Optional[str] = ..., approve: _Optional[bool] = ..., note: _Optional[str] = ...) -> None: ...

class ReviewMemoryResponse(_message.Message):
    __slots__ = ("memory",)
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    memory: InspectedMemory
    def __init__(self, memory: _Optional[_Union[InspectedMemory, _Mapping]] = ...) -> None: ...

class FeedbackRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "employee_id", "run_id", "kind", "text", "rating")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    RATING_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    employee_id: str
    run_id: str
    kind: FeedbackKind
    text: str
    rating: int
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., employee_id: _Optional[str] = ..., run_id: _Optional[str] = ..., kind: _Optional[_Union[FeedbackKind, str]] = ..., text: _Optional[str] = ..., rating: _Optional[int] = ...) -> None: ...

class FeedbackResponse(_message.Message):
    __slots__ = ("feedback_id", "proposal_id", "credited")
    FEEDBACK_ID_FIELD_NUMBER: _ClassVar[int]
    PROPOSAL_ID_FIELD_NUMBER: _ClassVar[int]
    CREDITED_FIELD_NUMBER: _ClassVar[int]
    feedback_id: str
    proposal_id: str
    credited: int
    def __init__(self, feedback_id: _Optional[str] = ..., proposal_id: _Optional[str] = ..., credited: _Optional[int] = ...) -> None: ...

class Proposal(_message.Message):
    __slots__ = ("proposal_id", "employee_id", "kind", "text", "risk", "status", "proposed_by", "decided_by", "decision_note", "feedback_id", "run_id", "created_at", "decided_at")
    PROPOSAL_ID_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    RISK_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PROPOSED_BY_FIELD_NUMBER: _ClassVar[int]
    DECIDED_BY_FIELD_NUMBER: _ClassVar[int]
    DECISION_NOTE_FIELD_NUMBER: _ClassVar[int]
    FEEDBACK_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    DECIDED_AT_FIELD_NUMBER: _ClassVar[int]
    proposal_id: str
    employee_id: str
    kind: str
    text: str
    risk: str
    status: str
    proposed_by: str
    decided_by: str
    decision_note: str
    feedback_id: str
    run_id: str
    created_at: _timestamp_pb2.Timestamp
    decided_at: _timestamp_pb2.Timestamp
    def __init__(self, proposal_id: _Optional[str] = ..., employee_id: _Optional[str] = ..., kind: _Optional[str] = ..., text: _Optional[str] = ..., risk: _Optional[str] = ..., status: _Optional[str] = ..., proposed_by: _Optional[str] = ..., decided_by: _Optional[str] = ..., decision_note: _Optional[str] = ..., feedback_id: _Optional[str] = ..., run_id: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., decided_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListProposalsRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "statuses")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    STATUSES_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    statuses: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., statuses: _Optional[_Iterable[str]] = ...) -> None: ...

class ListProposalsResponse(_message.Message):
    __slots__ = ("proposals",)
    PROPOSALS_FIELD_NUMBER: _ClassVar[int]
    proposals: _containers.RepeatedCompositeFieldContainer[Proposal]
    def __init__(self, proposals: _Optional[_Iterable[_Union[Proposal, _Mapping]]] = ...) -> None: ...

class DecideProposalRequest(_message.Message):
    __slots__ = ("tenant", "proposal_id", "approve", "note")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    PROPOSAL_ID_FIELD_NUMBER: _ClassVar[int]
    APPROVE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    proposal_id: str
    approve: bool
    note: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., proposal_id: _Optional[str] = ..., approve: _Optional[bool] = ..., note: _Optional[str] = ...) -> None: ...

class DecideProposalResponse(_message.Message):
    __slots__ = ("proposal", "rule")
    PROPOSAL_FIELD_NUMBER: _ClassVar[int]
    RULE_FIELD_NUMBER: _ClassVar[int]
    proposal: Proposal
    rule: Rule
    def __init__(self, proposal: _Optional[_Union[Proposal, _Mapping]] = ..., rule: _Optional[_Union[Rule, _Mapping]] = ...) -> None: ...

class Rule(_message.Message):
    __slots__ = ("rule_id", "employee_id", "text", "status", "proposal_id", "approved_by", "created_at", "revoked_by", "revoked_at")
    RULE_ID_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PROPOSAL_ID_FIELD_NUMBER: _ClassVar[int]
    APPROVED_BY_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    REVOKED_BY_FIELD_NUMBER: _ClassVar[int]
    REVOKED_AT_FIELD_NUMBER: _ClassVar[int]
    rule_id: str
    employee_id: str
    text: str
    status: str
    proposal_id: str
    approved_by: str
    created_at: _timestamp_pb2.Timestamp
    revoked_by: str
    revoked_at: _timestamp_pb2.Timestamp
    def __init__(self, rule_id: _Optional[str] = ..., employee_id: _Optional[str] = ..., text: _Optional[str] = ..., status: _Optional[str] = ..., proposal_id: _Optional[str] = ..., approved_by: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., revoked_by: _Optional[str] = ..., revoked_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListRulesRequest(_message.Message):
    __slots__ = ("tenant", "employee_id", "include_revoked")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_REVOKED_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    employee_id: str
    include_revoked: bool
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., employee_id: _Optional[str] = ..., include_revoked: _Optional[bool] = ...) -> None: ...

class ListRulesResponse(_message.Message):
    __slots__ = ("rules",)
    RULES_FIELD_NUMBER: _ClassVar[int]
    rules: _containers.RepeatedCompositeFieldContainer[Rule]
    def __init__(self, rules: _Optional[_Iterable[_Union[Rule, _Mapping]]] = ...) -> None: ...

class RevokeRuleRequest(_message.Message):
    __slots__ = ("tenant", "rule_id", "reason")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    RULE_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    rule_id: str
    reason: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., rule_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class RevokeRuleResponse(_message.Message):
    __slots__ = ("rule",)
    RULE_FIELD_NUMBER: _ClassVar[int]
    rule: Rule
    def __init__(self, rule: _Optional[_Union[Rule, _Mapping]] = ...) -> None: ...
