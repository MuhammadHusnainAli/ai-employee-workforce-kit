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

class ConversationKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONVERSATION_KIND_UNSPECIFIED: _ClassVar[ConversationKind]
    CONVERSATION_KIND_DIRECT: _ClassVar[ConversationKind]
    CONVERSATION_KIND_CALL: _ClassVar[ConversationKind]
    CONVERSATION_KIND_STANDUP: _ClassVar[ConversationKind]
CONVERSATION_KIND_UNSPECIFIED: ConversationKind
CONVERSATION_KIND_DIRECT: ConversationKind
CONVERSATION_KIND_CALL: ConversationKind
CONVERSATION_KIND_STANDUP: ConversationKind

class Participant(_message.Message):
    __slots__ = ("id", "kind")
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: str
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[str] = ...) -> None: ...

class Message(_message.Message):
    __slots__ = ("conversation_id", "seq", "message_id", "author", "author_kind", "kind", "text", "created_at")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_KIND_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    seq: int
    message_id: str
    author: str
    author_kind: str
    kind: str
    text: str
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, conversation_id: _Optional[str] = ..., seq: _Optional[int] = ..., message_id: _Optional[str] = ..., author: _Optional[str] = ..., author_kind: _Optional[str] = ..., kind: _Optional[str] = ..., text: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class Conversation(_message.Message):
    __slots__ = ("conversation_id", "kind", "topic", "status", "started_by", "participants", "turns", "max_turns", "last_seq", "last_message", "created_at", "updated_at")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TOPIC_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    STARTED_BY_FIELD_NUMBER: _ClassVar[int]
    PARTICIPANTS_FIELD_NUMBER: _ClassVar[int]
    TURNS_FIELD_NUMBER: _ClassVar[int]
    MAX_TURNS_FIELD_NUMBER: _ClassVar[int]
    LAST_SEQ_FIELD_NUMBER: _ClassVar[int]
    LAST_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    kind: ConversationKind
    topic: str
    status: str
    started_by: str
    participants: _containers.RepeatedCompositeFieldContainer[Participant]
    turns: int
    max_turns: int
    last_seq: int
    last_message: Message
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    def __init__(self, conversation_id: _Optional[str] = ..., kind: _Optional[_Union[ConversationKind, str]] = ..., topic: _Optional[str] = ..., status: _Optional[str] = ..., started_by: _Optional[str] = ..., participants: _Optional[_Iterable[_Union[Participant, _Mapping]]] = ..., turns: _Optional[int] = ..., max_turns: _Optional[int] = ..., last_seq: _Optional[int] = ..., last_message: _Optional[_Union[Message, _Mapping]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class SendMessageRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "to", "text")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    TO_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    to: str
    text: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., to: _Optional[str] = ..., text: _Optional[str] = ...) -> None: ...

class SendMessageResponse(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: Message
    def __init__(self, message: _Optional[_Union[Message, _Mapping]] = ...) -> None: ...

class PostMessageRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "conversation_id", "text")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    conversation_id: str
    text: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., conversation_id: _Optional[str] = ..., text: _Optional[str] = ...) -> None: ...

class PostMessageResponse(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: Message
    def __init__(self, message: _Optional[_Union[Message, _Mapping]] = ...) -> None: ...

class ListConversationsRequest(_message.Message):
    __slots__ = ("tenant", "participant", "kind", "limit")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    PARTICIPANT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    participant: str
    kind: ConversationKind
    limit: int
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., participant: _Optional[str] = ..., kind: _Optional[_Union[ConversationKind, str]] = ..., limit: _Optional[int] = ...) -> None: ...

class ListConversationsResponse(_message.Message):
    __slots__ = ("conversations",)
    CONVERSATIONS_FIELD_NUMBER: _ClassVar[int]
    conversations: _containers.RepeatedCompositeFieldContainer[Conversation]
    def __init__(self, conversations: _Optional[_Iterable[_Union[Conversation, _Mapping]]] = ...) -> None: ...

class GetConversationRequest(_message.Message):
    __slots__ = ("tenant", "conversation_id", "after_seq", "limit")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    AFTER_SEQ_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    conversation_id: str
    after_seq: int
    limit: int
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., conversation_id: _Optional[str] = ..., after_seq: _Optional[int] = ..., limit: _Optional[int] = ...) -> None: ...

class GetConversationResponse(_message.Message):
    __slots__ = ("conversation", "messages")
    CONVERSATION_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    conversation: Conversation
    messages: _containers.RepeatedCompositeFieldContainer[Message]
    def __init__(self, conversation: _Optional[_Union[Conversation, _Mapping]] = ..., messages: _Optional[_Iterable[_Union[Message, _Mapping]]] = ...) -> None: ...

class ReadInboxRequest(_message.Message):
    __slots__ = ("tenant",)
    TENANT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ...) -> None: ...

class ReadInboxResponse(_message.Message):
    __slots__ = ("messages",)
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    messages: _containers.RepeatedCompositeFieldContainer[Message]
    def __init__(self, messages: _Optional[_Iterable[_Union[Message, _Mapping]]] = ...) -> None: ...

class AskColleagueRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "colleague", "question", "conversation_id", "topic")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    COLLEAGUE_FIELD_NUMBER: _ClassVar[int]
    QUESTION_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    TOPIC_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    colleague: str
    question: str
    conversation_id: str
    topic: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., colleague: _Optional[str] = ..., question: _Optional[str] = ..., conversation_id: _Optional[str] = ..., topic: _Optional[str] = ...) -> None: ...

class AskColleagueResponse(_message.Message):
    __slots__ = ("conversation_id", "answer", "turns_left")
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    ANSWER_FIELD_NUMBER: _ClassVar[int]
    TURNS_LEFT_FIELD_NUMBER: _ClassVar[int]
    conversation_id: str
    answer: str
    turns_left: int
    def __init__(self, conversation_id: _Optional[str] = ..., answer: _Optional[str] = ..., turns_left: _Optional[int] = ...) -> None: ...

class StandupReport(_message.Message):
    __slots__ = ("employee_id", "title", "done", "next", "blockers", "idle")
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    DONE_FIELD_NUMBER: _ClassVar[int]
    NEXT_FIELD_NUMBER: _ClassVar[int]
    BLOCKERS_FIELD_NUMBER: _ClassVar[int]
    IDLE_FIELD_NUMBER: _ClassVar[int]
    employee_id: str
    title: str
    done: str
    next: str
    blockers: str
    idle: bool
    def __init__(self, employee_id: _Optional[str] = ..., title: _Optional[str] = ..., done: _Optional[str] = ..., next: _Optional[str] = ..., blockers: _Optional[str] = ..., idle: _Optional[bool] = ...) -> None: ...

class NextStep(_message.Message):
    __slots__ = ("owner", "action", "task_id")
    OWNER_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    owner: str
    action: str
    task_id: str
    def __init__(self, owner: _Optional[str] = ..., action: _Optional[str] = ..., task_id: _Optional[str] = ...) -> None: ...

class Standup(_message.Message):
    __slots__ = ("standup_id", "department_id", "status", "led_by", "started_by", "discussion_s", "assign_next_steps", "reports", "where_we_are", "where_we_go", "next_steps", "risks", "error", "created_at", "finished_at")
    STANDUP_ID_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LED_BY_FIELD_NUMBER: _ClassVar[int]
    STARTED_BY_FIELD_NUMBER: _ClassVar[int]
    DISCUSSION_S_FIELD_NUMBER: _ClassVar[int]
    ASSIGN_NEXT_STEPS_FIELD_NUMBER: _ClassVar[int]
    REPORTS_FIELD_NUMBER: _ClassVar[int]
    WHERE_WE_ARE_FIELD_NUMBER: _ClassVar[int]
    WHERE_WE_GO_FIELD_NUMBER: _ClassVar[int]
    NEXT_STEPS_FIELD_NUMBER: _ClassVar[int]
    RISKS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    FINISHED_AT_FIELD_NUMBER: _ClassVar[int]
    standup_id: str
    department_id: str
    status: str
    led_by: str
    started_by: str
    discussion_s: int
    assign_next_steps: bool
    reports: _containers.RepeatedCompositeFieldContainer[StandupReport]
    where_we_are: str
    where_we_go: str
    next_steps: _containers.RepeatedCompositeFieldContainer[NextStep]
    risks: _containers.RepeatedScalarFieldContainer[str]
    error: str
    created_at: _timestamp_pb2.Timestamp
    finished_at: _timestamp_pb2.Timestamp
    def __init__(self, standup_id: _Optional[str] = ..., department_id: _Optional[str] = ..., status: _Optional[str] = ..., led_by: _Optional[str] = ..., started_by: _Optional[str] = ..., discussion_s: _Optional[int] = ..., assign_next_steps: _Optional[bool] = ..., reports: _Optional[_Iterable[_Union[StandupReport, _Mapping]]] = ..., where_we_are: _Optional[str] = ..., where_we_go: _Optional[str] = ..., next_steps: _Optional[_Iterable[_Union[NextStep, _Mapping]]] = ..., risks: _Optional[_Iterable[str]] = ..., error: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., finished_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class StartStandupRequest(_message.Message):
    __slots__ = ("tenant", "idempotency_key", "department_id", "discussion_s", "assign_next_steps")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    DISCUSSION_S_FIELD_NUMBER: _ClassVar[int]
    ASSIGN_NEXT_STEPS_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    idempotency_key: _common_pb2.IdempotencyKey
    department_id: str
    discussion_s: int
    assign_next_steps: bool
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., idempotency_key: _Optional[_Union[_common_pb2.IdempotencyKey, _Mapping]] = ..., department_id: _Optional[str] = ..., discussion_s: _Optional[int] = ..., assign_next_steps: _Optional[bool] = ...) -> None: ...

class StartStandupResponse(_message.Message):
    __slots__ = ("standup",)
    STANDUP_FIELD_NUMBER: _ClassVar[int]
    standup: Standup
    def __init__(self, standup: _Optional[_Union[Standup, _Mapping]] = ...) -> None: ...

class GetStandupRequest(_message.Message):
    __slots__ = ("tenant", "standup_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    STANDUP_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    standup_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., standup_id: _Optional[str] = ...) -> None: ...

class GetStandupResponse(_message.Message):
    __slots__ = ("standup",)
    STANDUP_FIELD_NUMBER: _ClassVar[int]
    standup: Standup
    def __init__(self, standup: _Optional[_Union[Standup, _Mapping]] = ...) -> None: ...

class ListStandupsRequest(_message.Message):
    __slots__ = ("tenant", "limit")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    limit: int
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., limit: _Optional[int] = ...) -> None: ...

class ListStandupsResponse(_message.Message):
    __slots__ = ("standups",)
    STANDUPS_FIELD_NUMBER: _ClassVar[int]
    standups: _containers.RepeatedCompositeFieldContainer[Standup]
    def __init__(self, standups: _Optional[_Iterable[_Union[Standup, _Mapping]]] = ...) -> None: ...

class EndStandupDiscussionRequest(_message.Message):
    __slots__ = ("tenant", "standup_id")
    TENANT_FIELD_NUMBER: _ClassVar[int]
    STANDUP_ID_FIELD_NUMBER: _ClassVar[int]
    tenant: _common_pb2.TenantContext
    standup_id: str
    def __init__(self, tenant: _Optional[_Union[_common_pb2.TenantContext, _Mapping]] = ..., standup_id: _Optional[str] = ...) -> None: ...

class EndStandupDiscussionResponse(_message.Message):
    __slots__ = ("standup",)
    STANDUP_FIELD_NUMBER: _ClassVar[int]
    standup: Standup
    def __init__(self, standup: _Optional[_Union[Standup, _Mapping]] = ...) -> None: ...
