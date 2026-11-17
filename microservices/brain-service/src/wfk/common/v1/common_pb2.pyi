from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ActorKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ACTOR_KIND_UNSPECIFIED: _ClassVar[ActorKind]
    ACTOR_KIND_HUMAN: _ClassVar[ActorKind]
    ACTOR_KIND_EMPLOYEE: _ClassVar[ActorKind]
    ACTOR_KIND_SERVICE: _ClassVar[ActorKind]

class ErrorClass(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ERROR_CLASS_UNSPECIFIED: _ClassVar[ErrorClass]
    ERROR_CLASS_RETRYABLE: _ClassVar[ErrorClass]
    ERROR_CLASS_NON_RETRYABLE: _ClassVar[ErrorClass]
    ERROR_CLASS_UNKNOWN_OUTCOME: _ClassVar[ErrorClass]
ACTOR_KIND_UNSPECIFIED: ActorKind
ACTOR_KIND_HUMAN: ActorKind
ACTOR_KIND_EMPLOYEE: ActorKind
ACTOR_KIND_SERVICE: ActorKind
ERROR_CLASS_UNSPECIFIED: ErrorClass
ERROR_CLASS_RETRYABLE: ErrorClass
ERROR_CLASS_NON_RETRYABLE: ErrorClass
ERROR_CLASS_UNKNOWN_OUTCOME: ErrorClass

class TenantContext(_message.Message):
    __slots__ = ("tenant_id", "cell_id", "employee_id", "actor")
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    CELL_ID_FIELD_NUMBER: _ClassVar[int]
    EMPLOYEE_ID_FIELD_NUMBER: _ClassVar[int]
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    tenant_id: str
    cell_id: str
    employee_id: str
    actor: Actor
    def __init__(self, tenant_id: _Optional[str] = ..., cell_id: _Optional[str] = ..., employee_id: _Optional[str] = ..., actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class Actor(_message.Message):
    __slots__ = ("kind", "id")
    KIND_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    kind: ActorKind
    id: str
    def __init__(self, kind: _Optional[_Union[ActorKind, str]] = ..., id: _Optional[str] = ...) -> None: ...

class IdempotencyKey(_message.Message):
    __slots__ = ("value",)
    VALUE_FIELD_NUMBER: _ClassVar[int]
    value: str
    def __init__(self, value: _Optional[str] = ...) -> None: ...

class ErrorDetail(_message.Message):
    __slots__ = ("error_class", "code", "message", "retry_after_ms")
    ERROR_CLASS_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRY_AFTER_MS_FIELD_NUMBER: _ClassVar[int]
    error_class: ErrorClass
    code: str
    message: str
    retry_after_ms: int
    def __init__(self, error_class: _Optional[_Union[ErrorClass, str]] = ..., code: _Optional[str] = ..., message: _Optional[str] = ..., retry_after_ms: _Optional[int] = ...) -> None: ...

class PageRequest(_message.Message):
    __slots__ = ("page_size", "page_token")
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    page_size: int
    page_token: str
    def __init__(self, page_size: _Optional[int] = ..., page_token: _Optional[str] = ...) -> None: ...

class PageResponse(_message.Message):
    __slots__ = ("next_page_token",)
    NEXT_PAGE_TOKEN_FIELD_NUMBER: _ClassVar[int]
    next_page_token: str
    def __init__(self, next_page_token: _Optional[str] = ...) -> None: ...
