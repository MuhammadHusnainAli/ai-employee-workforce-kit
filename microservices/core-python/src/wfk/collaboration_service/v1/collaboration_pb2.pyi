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
