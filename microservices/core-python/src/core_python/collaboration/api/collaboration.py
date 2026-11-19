"""CollaborationService over gRPC (contracts/proto/wfk/collaboration_service/v1)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, TypeVar

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from psycopg.rows import DictRow

from wfk.collaboration_service.v1 import collaboration_pb2 as pb
from wfk.collaboration_service.v1 import collaboration_pb2_grpc
from wfk.common.v1 import common_pb2

from core_python.collaboration.adapters.answerer import Answerer
from core_python.collaboration.adapters.colleagues import Colleagues
from core_python.collaboration.adapters.postgres.conversations import (
    PERSON_WHO,
    Conversations,
    Who,
)
from core_python.collaboration.adapters.postgres.standups import Standups
from core_python.collaboration.domain.conversations import MAX_CALL_TURNS, PERSON, speaker
from core_python.collaboration.domain.errors import Closed, Invalid, NotAParticipant, NotFound
from core_python.collaboration.domain.standup import DISCUSSION_S

T = TypeVar("T")
MAX_TEXT = 20_000
KIND_TO_PB = {
    "direct": pb.CONVERSATION_KIND_DIRECT,
    "call": pb.CONVERSATION_KIND_CALL,
    "standup": pb.CONVERSATION_KIND_STANDUP,
}
KIND_FROM_PB = {v: k for k, v in KIND_TO_PB.items()}


async def _abort(context: grpc.aio.ServicerContext, code: grpc.StatusCode, msg: str) -> NoReturn:
    await context.abort(code, msg)
    raise AssertionError("unreachable")


def _ts(value: Any) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(value)
    return ts


def tenant_of(t: common_pb2.TenantContext) -> str:
    try:
        return str(uuid.UUID(t.tenant_id))
    except ValueError:
        raise Invalid("tenant.tenant_id must be a UUID") from None


def who(t: common_pb2.TenantContext) -> Who:
    """The speaker: an employee (actor kind EMPLOYEE) or the person (anyone else)."""
    if t.actor.kind == common_pb2.ACTOR_KIND_EMPLOYEE:
        if not t.actor.id:
            raise Invalid("an employee caller needs actor.id")
        return Who(*speaker("employee", t.actor.id))
    return Who(*speaker("person", t.actor.id))


def text_of(value: str) -> str:
    text = value.strip()
    if not text or len(text) > MAX_TEXT:
        raise Invalid(f"text is required (at most {MAX_TEXT:,} characters)")
    return text


def key_of(k: common_pb2.IdempotencyKey) -> str:
    if not k.value or len(k.value) > 200:
        raise Invalid("idempotency_key is required (at most 200 characters)")
    return k.value


def conversation_id_of(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise Invalid("conversation_id must be a UUID") from None


def message_pb(row: DictRow) -> pb.Message:
    return pb.Message(
        conversation_id=str(row["conversation_id"]),
        seq=row["seq"],
        message_id=str(row["message_id"]),
        author=row["author"],
        author_kind=row["author_kind"],
        kind=row["kind"],
        text=row["text"],
        created_at=_ts(row["created_at"]),
    )


def conversation_pb(
    row: DictRow, participants: list[DictRow], last: DictRow | None = None
) -> pb.Conversation:
    c = pb.Conversation(
        conversation_id=str(row["conversation_id"]),
        kind=KIND_TO_PB[row["kind"]],
        topic=row["topic"],
        status=row["status"],
        started_by=row["started_by"],
        participants=[pb.Participant(id=p["participant"], kind=p["kind"]) for p in participants],
        turns=row["turns"],
        max_turns=row["max_turns"],
        last_seq=row["last_seq"],
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )
    if last is not None:
        c.last_message.CopyFrom(message_pb(last))
    return c


def standup_pb(row: DictRow) -> pb.Standup:
    summary = row["summary"] or {}
    s = pb.Standup(
        standup_id=str(row["standup_id"]),
        department_id=row["department_id"],
        status=row["status"],
        led_by=row["led_by"],
        started_by=row["started_by"],
        discussion_s=row["discussion_s"],
        assign_next_steps=row["assign_next_steps"],
        reports=[
            pb.StandupReport(
                employee_id=r.get("employee_id", ""),
                title=r.get("title", ""),
                done=r.get("done", ""),
                next=r.get("next", ""),
                blockers=r.get("blockers", ""),
                idle=bool(r.get("idle")),
            )
            for r in row["reports"]
        ],
        where_we_are=summary.get("where_we_are", ""),
        where_we_go=summary.get("where_we_go", ""),
        next_steps=[
            pb.NextStep(
                owner=n.get("owner", ""), action=n.get("action", ""), task_id=n.get("task_id", "")
            )
            for n in summary.get("next_steps", [])
        ],
        risks=list(summary.get("risks", [])),
        error=row["error"],
        created_at=_ts(row["created_at"]),
    )
    if row["finished_at"] is not None:
        s.finished_at.CopyFrom(_ts(row["finished_at"]))
    return s


class CollaborationServicer(collaboration_pb2_grpc.CollaborationServiceServicer):
    def __init__(
        self,
        conversations: Conversations,
        answerer: Answerer,
        colleagues: Colleagues,
        standups: Standups,
    ) -> None:
        self.conversations = conversations
        self.answerer = answerer
        self.colleagues = colleagues
        self.standups = standups

    async def _guard(
        self, context: grpc.aio.ServicerContext, call: Callable[[], Awaitable[T]]
    ) -> T:
        try:
            return await call()
        except Invalid as exc:
            await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except NotFound as exc:
            await _abort(context, grpc.StatusCode.NOT_FOUND, str(exc))
        except Closed as exc:
            await _abort(context, grpc.StatusCode.FAILED_PRECONDITION, str(exc))
        except NotAParticipant as exc:
            await _abort(context, grpc.StatusCode.PERMISSION_DENIED, str(exc))

    async def SendMessage(
        self, request: pb.SendMessageRequest, context: grpc.aio.ServicerContext
    ) -> pb.SendMessageResponse:
        async def call() -> pb.SendMessageResponse:
            tenant, frm = tenant_of(request.tenant), who(request.tenant)
            to = request.to.strip()
            if not to:
                raise Invalid("to is required: an employee id or 'person'")
            target = PERSON_WHO if to == PERSON else Who(to, "employee", to)
            row = await self.conversations.send(
                tenant, frm, target, text_of(request.text), key_of(request.idempotency_key)
            )
            return pb.SendMessageResponse(message=message_pb(row))

        return await self._guard(context, call)

    async def PostMessage(
        self, request: pb.PostMessageRequest, context: grpc.aio.ServicerContext
    ) -> pb.PostMessageResponse:
        async def call() -> pb.PostMessageResponse:
            row = await self.conversations.post(
                tenant_of(request.tenant),
                conversation_id_of(request.conversation_id),
                who(request.tenant),
                text_of(request.text),
                key_of(request.idempotency_key),
            )
            return pb.PostMessageResponse(message=message_pb(row))

        return await self._guard(context, call)

    async def ListConversations(
        self, request: pb.ListConversationsRequest, context: grpc.aio.ServicerContext
    ) -> pb.ListConversationsResponse:
        async def call() -> pb.ListConversationsResponse:
            limit = request.limit or 50
            if not 1 <= limit <= 200:
                raise Invalid("limit must be 1 to 200")
            rows = await self.conversations.list(
                tenant_of(request.tenant),
                participant=request.participant or None,
                kind=KIND_FROM_PB.get(request.kind),
                limit=limit,
            )
            return pb.ListConversationsResponse(
                conversations=[conversation_pb(c, p, last) for c, p, last in rows]
            )

        return await self._guard(context, call)

    async def GetConversation(
        self, request: pb.GetConversationRequest, context: grpc.aio.ServicerContext
    ) -> pb.GetConversationResponse:
        async def call() -> pb.GetConversationResponse:
            limit = request.limit or 200
            if not 1 <= limit <= 500:
                raise Invalid("limit must be 1 to 500")
            thread = await self.conversations.thread(
                tenant_of(request.tenant),
                conversation_id_of(request.conversation_id),
                max(request.after_seq, 0),
                limit,
            )
            return pb.GetConversationResponse(
                conversation=conversation_pb(thread.conversation, thread.participants),
                messages=[message_pb(m) for m in thread.messages],
            )

        return await self._guard(context, call)

    async def ReadInbox(
        self, request: pb.ReadInboxRequest, context: grpc.aio.ServicerContext
    ) -> pb.ReadInboxResponse:
        async def call() -> pb.ReadInboxResponse:
            rows = await self.conversations.inbox(
                tenant_of(request.tenant), who(request.tenant).participant
            )
            return pb.ReadInboxResponse(messages=[message_pb(r) for r in rows])

        return await self._guard(context, call)

    async def AskColleague(
        self, request: pb.AskColleagueRequest, context: grpc.aio.ServicerContext
    ) -> pb.AskColleagueResponse:
        async def call() -> pb.AskColleagueResponse:
            tenant, caller = tenant_of(request.tenant), who(request.tenant)
            key, question = key_of(request.idempotency_key), text_of(request.question)
            name = request.colleague.strip()
            if name == caller.participant:
                raise Invalid("ask someone else: you cannot call yourself")
            colleague = await self.colleagues.get(tenant, name)
            if colleague is None:
                raise NotFound(f"no employee {name!r} to call")
            if colleague.status != "active":
                raise Closed(f"{name} is {colleague.status} and cannot take calls")
            turn = await self.conversations.ask(
                tenant,
                caller,
                name,
                question,
                key,
                conversation_id=conversation_id_of(request.conversation_id)
                if request.conversation_id
                else None,
                topic=request.topic.strip() or question[:120],
            )
            answer = turn.answer
            if answer is None:
                answer = await self.answerer.answer(
                    colleague, caller.author, turn.transcript, question
                )
                left = await self.conversations.answer(
                    tenant, turn.conversation_id, name, answer, key
                )
            else:
                left = max(MAX_CALL_TURNS - turn.turns, 0)
            return pb.AskColleagueResponse(
                conversation_id=turn.conversation_id, answer=answer, turns_left=left
            )

        return await self._guard(context, call)

    # ---- standups --------------------------------------------------------------------------

    async def StartStandup(
        self, request: pb.StartStandupRequest, context: grpc.aio.ServicerContext
    ) -> pb.StartStandupResponse:
        async def call() -> pb.StartStandupResponse:
            seconds = request.discussion_s if request.HasField("discussion_s") else DISCUSSION_S
            if not 0 <= seconds <= 3600:
                raise Invalid("discussion_s must be 0 to 3600")
            if len(request.department_id) > 63:
                raise Invalid("department_id is at most 63 characters")
            row = await self.standups.start(
                tenant_of(request.tenant),
                key_of(request.idempotency_key),
                department_id=request.department_id,
                discussion_s=seconds,
                assign_next_steps=request.assign_next_steps,
                started_by=who(request.tenant).author,
            )
            return pb.StartStandupResponse(standup=standup_pb(row))

        return await self._guard(context, call)

    async def GetStandup(
        self, request: pb.GetStandupRequest, context: grpc.aio.ServicerContext
    ) -> pb.GetStandupResponse:
        async def call() -> pb.GetStandupResponse:
            row = await self.standups.get(
                tenant_of(request.tenant), conversation_id_of(request.standup_id)
            )
            return pb.GetStandupResponse(standup=standup_pb(row))

        return await self._guard(context, call)

    async def ListStandups(
        self, request: pb.ListStandupsRequest, context: grpc.aio.ServicerContext
    ) -> pb.ListStandupsResponse:
        async def call() -> pb.ListStandupsResponse:
            limit = request.limit or 20
            if not 1 <= limit <= 100:
                raise Invalid("limit must be 1 to 100")
            rows = await self.standups.list(tenant_of(request.tenant), limit)
            return pb.ListStandupsResponse(standups=[standup_pb(r) for r in rows])

        return await self._guard(context, call)

    async def EndStandupDiscussion(
        self, request: pb.EndStandupDiscussionRequest, context: grpc.aio.ServicerContext
    ) -> pb.EndStandupDiscussionResponse:
        async def call() -> pb.EndStandupDiscussionResponse:
            row = await self.standups.request_end(
                tenant_of(request.tenant), conversation_id_of(request.standup_id)
            )
            return pb.EndStandupDiscussionResponse(standup=standup_pb(row))

        return await self._guard(context, call)
