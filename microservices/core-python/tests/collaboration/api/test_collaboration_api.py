"""CollaborationService over real gRPC, on a migrated collab_db (just up). The model is scripted."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import grpc
import pytest
from agent_harness import FakeProvider
from agent_harness.llm_providers import CompletionRequest

from wfk.collaboration_service.v1 import collaboration_pb2 as pb
from wfk.collaboration_service.v1 import collaboration_pb2_grpc
from wfk.common.v1 import common_pb2
from wfk_platform.db import Database

from core_python.collaboration.adapters.answerer import Answerer
from core_python.collaboration.adapters.colleagues import StaticColleagues
from core_python.collaboration.app import create_server
from core_python.collaboration.domain.conversations import Colleague
from core_python.collaboration.settings import Settings

from ..conftest import OTHER_TENANT, TENANT

pytestmark = pytest.mark.integration
Stub = collaboration_pb2_grpc.CollaborationServiceStub

COLLEAGUES = {
    eid: Colleague(employee_id=eid, name=eid.title(), title=title, role=title, model="m")
    for eid, title in [
        ("sales-head", "Head of Sales"),
        ("sales-junior", "Junior Sales"),
        ("marketing", "Head of Marketing"),
    ]
}


@dataclass
class Rig:
    stub: Stub
    model: FakeProvider


def answer(req: CompletionRequest) -> str:
    return f"answer {len(req.messages)}"


@pytest.fixture
async def rig(collab_db: Database) -> AsyncIterator[Rig]:
    model = FakeProvider([answer], loop=True)
    server, _, port = create_server(
        Settings(host="127.0.0.1"),
        collab_db,
        port=0,
        answerer=Answerer(model),
        colleagues=StaticColleagues(COLLEAGUES),
    )
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield Rig(collaboration_pb2_grpc.CollaborationServiceStub(channel), model)
    await server.stop(grace=None)


def as_employee(eid: str, t: str = TENANT) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(
        tenant_id=t,
        employee_id=eid,
        actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_EMPLOYEE, id=eid),
    )


def as_person(t: str = TENANT) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(
        tenant_id=t, actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_HUMAN, id="maria")
    )


def key(v: str = "") -> common_pb2.IdempotencyKey:
    return common_pb2.IdempotencyKey(value=v or uuid.uuid4().hex)


async def code(call: object) -> grpc.StatusCode:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await call  # type: ignore[misc]
    return caught.value.code()


def fresh() -> str:
    return str(uuid.uuid4())


async def send(s: Stub, t: str, frm: str, to: str, text: str, k: str = "") -> pb.Message:
    got = await s.SendMessage(
        pb.SendMessageRequest(tenant=as_employee(frm, t), idempotency_key=key(k), to=to, text=text)
    )
    return got.message


async def test_two_employees_share_one_direct_conversation(rig: Rig) -> None:
    t = fresh()
    first = await send(rig.stub, t, "sales-junior", "marketing", "Do we have a price sheet?", "k1")
    again = await send(rig.stub, t, "sales-junior", "marketing", "Do we have a price sheet?", "k1")
    assert again.message_id == first.message_id, "same key, same message"
    reply = await send(rig.stub, t, "marketing", "sales-junior", "Yes, in the drive.")
    assert reply.conversation_id == first.conversation_id
    assert (reply.seq, reply.author, reply.author_kind) == (2, "marketing", "employee")

    # The person reads every conversation, even ones it is not part of.
    got = await rig.stub.GetConversation(
        pb.GetConversationRequest(tenant=as_person(t), conversation_id=first.conversation_id)
    )
    assert got.conversation.kind == pb.CONVERSATION_KIND_DIRECT
    assert {p.id for p in got.conversation.participants} == {"sales-junior", "marketing"}
    assert [m.text for m in got.messages] == ["Do we have a price sheet?", "Yes, in the drive."]
    newer = await rig.stub.GetConversation(
        pb.GetConversationRequest(
            tenant=as_person(t), conversation_id=first.conversation_id, after_seq=1
        )
    )
    assert [m.seq for m in newer.messages] == [2]

    mine = await rig.stub.ListConversations(
        pb.ListConversationsRequest(tenant=as_person(t), participant="marketing")
    )
    assert [c.conversation_id for c in mine.conversations] == [first.conversation_id]
    assert mine.conversations[0].last_message.text == "Yes, in the drive."
    other = await rig.stub.ListConversations(
        pb.ListConversationsRequest(tenant=as_person(OTHER_TENANT))
    )
    assert first.conversation_id not in {c.conversation_id for c in other.conversations}


async def test_an_employee_reads_its_inbox_once(rig: Rig) -> None:
    t = fresh()
    await send(rig.stub, t, "sales-head", "sales-junior", "Call the Acme lead today.")
    await send(rig.stub, t, "marketing", "sales-junior", "New price sheet is out.")
    await send(rig.stub, t, "sales-junior", "marketing", "Thanks!")
    inbox = await rig.stub.ReadInbox(pb.ReadInboxRequest(tenant=as_employee("sales-junior", t)))
    assert sorted(m.text for m in inbox.messages) == [
        "Call the Acme lead today.",
        "New price sheet is out.",
    ]
    again = await rig.stub.ReadInbox(pb.ReadInboxRequest(tenant=as_employee("sales-junior", t)))
    assert list(again.messages) == []
    theirs = await rig.stub.ReadInbox(pb.ReadInboxRequest(tenant=as_employee("marketing", t)))
    assert [m.text for m in theirs.messages] == ["Thanks!"]


async def test_messages_to_the_person_and_to_nobody(rig: Rig) -> None:
    t = fresh()
    msg = await send(rig.stub, t, "sales-head", "person", "We need a budget decision.")
    convo = await rig.stub.GetConversation(
        pb.GetConversationRequest(tenant=as_person(t), conversation_id=msg.conversation_id)
    )
    assert {p.kind for p in convo.conversation.participants} == {"employee", "person"}
    req = pb.SendMessageRequest(
        tenant=as_employee("sales-head", t), idempotency_key=key(), to="sales-head", text="hi me"
    )
    assert await code(rig.stub.SendMessage(req)) == grpc.StatusCode.INVALID_ARGUMENT
    req = pb.SendMessageRequest(
        tenant=as_employee("sales-head", t), idempotency_key=key(), to="sales-head", text=""
    )
    assert await code(rig.stub.SendMessage(req)) == grpc.StatusCode.INVALID_ARGUMENT


async def test_a_call_answers_in_role_carries_on_and_closes_after_six_turns(rig: Rig) -> None:
    t = fresh()

    def ask(q: str, convo: str = "", k: str = "") -> Any:
        return rig.stub.AskColleague(
            pb.AskColleagueRequest(
                tenant=as_employee("sales-junior", t),
                idempotency_key=key(k),
                colleague="marketing",
                question=q,
                conversation_id=convo,
                topic="Price sheet",
            )
        )

    first = await ask("Is there a price sheet?", k="q1")
    assert (first.answer, first.turns_left) == ("answer 1", 5)
    system = rig.model.requests[-1].system or ""
    assert "Head of Marketing" in str(system) and "sales-junior" in str(system)
    calls = len(rig.model.requests)
    retry = await ask("Is there a price sheet?", k="q1")
    assert retry.answer == first.answer and len(rig.model.requests) == calls, "no second model call"

    convo = first.conversation_id
    second = await ask("Where is it?", convo)
    assert second.answer == "answer 3", "the callee sees the call so far"
    for i in range(4):
        await ask(f"More {i}?", convo)
    assert await code(ask("One more?", convo)) == grpc.StatusCode.FAILED_PRECONDITION

    got = await rig.stub.GetConversation(
        pb.GetConversationRequest(tenant=as_person(t), conversation_id=convo)
    )
    assert got.conversation.status == "closed" and got.conversation.turns == 6
    assert [m.kind for m in got.messages[:2]] == ["question", "answer"]
    assert got.messages[-1].kind == "system"
    # A closed call takes nothing more, not even from the person.
    post = pb.PostMessageRequest(
        tenant=as_person(t), idempotency_key=key(), conversation_id=convo, text="late"
    )
    assert await code(rig.stub.PostMessage(post)) == grpc.StatusCode.FAILED_PRECONDITION
    after = await rig.stub.GetConversation(
        pb.GetConversationRequest(tenant=as_person(t), conversation_id=convo)
    )
    assert len(after.messages) == len(got.messages)


async def test_bad_calls_are_refused(rig: Rig) -> None:
    t = fresh()

    def ask(colleague: str, convo: str = "") -> Any:
        return rig.stub.AskColleague(
            pb.AskColleagueRequest(
                tenant=as_employee("sales-junior", t),
                idempotency_key=key(),
                colleague=colleague,
                question="Hi?",
                conversation_id=convo,
            )
        )

    assert await code(ask("ghost")) == grpc.StatusCode.NOT_FOUND
    assert await code(ask("sales-junior")) == grpc.StatusCode.INVALID_ARGUMENT
    msg = await send(rig.stub, t, "sales-junior", "marketing", "hello")
    assert await code(ask("marketing", msg.conversation_id)) == grpc.StatusCode.INVALID_ARGUMENT


async def test_the_person_joins_a_conversation_and_outsiders_cannot_post(rig: Rig) -> None:
    t = fresh()
    msg = await send(rig.stub, t, "sales-head", "marketing", "Campaign next week?")
    joined = await rig.stub.PostMessage(
        pb.PostMessageRequest(
            tenant=as_person(t),
            idempotency_key=key(),
            conversation_id=msg.conversation_id,
            text="Yes, go.",
        )
    )
    assert (joined.message.author, joined.message.author_kind) == ("maria", "person")
    outsider = pb.PostMessageRequest(
        tenant=as_employee("sales-junior", t),
        idempotency_key=key(),
        conversation_id=msg.conversation_id,
        text="me too",
    )
    assert await code(rig.stub.PostMessage(outsider)) == grpc.StatusCode.PERMISSION_DENIED
    elsewhere = pb.GetConversationRequest(
        tenant=as_person(OTHER_TENANT), conversation_id=msg.conversation_id
    )
    assert await code(rig.stub.GetConversation(elsewhere)) == grpc.StatusCode.NOT_FOUND


async def test_standups_start_once_list_and_end(rig: Rig, collab_db: Database) -> None:
    t = fresh()
    k = key()
    start = pb.StartStandupRequest(
        tenant=as_person(t), idempotency_key=k, department_id="sales", assign_next_steps=True
    )
    first = (await rig.stub.StartStandup(start)).standup
    again = (await rig.stub.StartStandup(start)).standup
    assert again.standup_id == first.standup_id
    assert (first.status, first.department_id, first.discussion_s) == ("collecting", "sales", 120)
    assert first.started_by == "maria" and first.assign_next_steps

    convo = await rig.stub.GetConversation(
        pb.GetConversationRequest(tenant=as_person(t), conversation_id=first.standup_id)
    )
    assert convo.conversation.kind == pb.CONVERSATION_KIND_STANDUP
    got = await rig.stub.GetStandup(
        pb.GetStandupRequest(tenant=as_person(t), standup_id=first.standup_id)
    )
    assert got.standup.standup_id == first.standup_id
    listed = await rig.stub.ListStandups(pb.ListStandupsRequest(tenant=as_person(t)))
    assert [s.standup_id for s in listed.standups] == [first.standup_id]

    await rig.stub.EndStandupDiscussion(
        pb.EndStandupDiscussionRequest(tenant=as_person(t), standup_id=first.standup_id)
    )
    async with collab_db.tx() as conn:
        cur = await conn.execute(
            "SELECT type FROM outbox WHERE subject = %s AND type LIKE 'collab.standup%%'"
            " ORDER BY seq",
            (first.standup_id,),
        )
        assert [r["type"] for r in await cur.fetchall()] == [
            "collab.standup_started",
            "collab.standup_end_requested",
        ]

    quiet = pb.StartStandupRequest(tenant=as_person(t), idempotency_key=key(), discussion_s=0)
    assert (await rig.stub.StartStandup(quiet)).standup.discussion_s == 0
    too_long = pb.StartStandupRequest(tenant=as_person(t), idempotency_key=key(), discussion_s=4000)
    assert await code(rig.stub.StartStandup(too_long)) == grpc.StatusCode.INVALID_ARGUMENT


async def test_a_finished_standup_cannot_be_ended(rig: Rig, collab_db: Database) -> None:
    from core_python.collaboration.adapters.postgres.conversations import Conversations
    from core_python.collaboration.adapters.postgres.standups import Standups

    t = fresh()
    started = await rig.stub.StartStandup(
        pb.StartStandupRequest(tenant=as_person(t), idempotency_key=key())
    )
    sid = started.standup.standup_id
    store = Standups(collab_db, "//test", Conversations(collab_db, "//test"))
    await store.fail(t, sid, "no co-CEO")
    end = pb.EndStandupDiscussionRequest(tenant=as_person(t), standup_id=sid)
    assert await code(rig.stub.EndStandupDiscussion(end)) == grpc.StatusCode.FAILED_PRECONDITION
    got = await rig.stub.GetStandup(pb.GetStandupRequest(tenant=as_person(t), standup_id=sid))
    assert (got.standup.status, got.standup.error) == ("failed", "no co-CEO")
