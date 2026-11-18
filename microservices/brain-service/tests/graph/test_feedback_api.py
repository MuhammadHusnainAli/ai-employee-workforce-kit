"""Brain.Feedback over real gRPC, on a migrated brain_db (just up)."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import grpc
import pytest

from wfk.brain_service.v1 import brain_pb2, brain_pb2_grpc
from wfk.common.v1 import common_pb2
from wfk_platform.db import Database

from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.app import create_server
from brain_service.domain.write.model import Candidate, Source
from brain_service.recall import Recall, RecallRequest
from brain_service.remember import Remember
from brain_service.settings import Settings

from ..conftest import TENANT

pytestmark = pytest.mark.integration
EMBED = HashEmbedder()
HUMAN, EMPLOYEE = common_pb2.ACTOR_KIND_HUMAN, common_pb2.ACTOR_KIND_EMPLOYEE


@pytest.fixture
async def stub(brain_db: Database) -> AsyncIterator[brain_pb2_grpc.BrainServiceStub]:
    server, _, port = create_server(Settings(host="127.0.0.1"), Recall(brain_db, EMBED), port=0)
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as ch:
        yield brain_pb2_grpc.BrainServiceStub(ch)
    await server.stop(None)


@pytest.fixture
def emp() -> str:
    return f"emp-{uuid.uuid4().hex[:8]}"


def req(emp: str, kind: Any, *, actor: Any = HUMAN, key: str | None = None,
        **kw: Any) -> brain_pb2.FeedbackRequest:  # fmt: skip
    return brain_pb2.FeedbackRequest(
        tenant=common_pb2.TenantContext(tenant_id=TENANT,
                                        actor=common_pb2.Actor(kind=actor, id="maria")),
        idempotency_key=common_pb2.IdempotencyKey(value=key or uuid.uuid4().hex),
        employee_id=emp, kind=kind, **kw,
    )  # fmt: skip


async def rows(db: Database, sql: str, *args: Any) -> list[dict[str, Any]]:
    async with db.tenant_tx(TENANT) as conn:
        cur = await conn.execute(sql, args)  # type: ignore[arg-type]
        return [dict(r) for r in await cur.fetchall()]


async def test_a_persons_correction_proposes_a_rule_once(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    r = req(emp, brain_pb2.FEEDBACK_KIND_CORRECTION, key="c-1",
            text="Never CC finance. The staging token is sk-abcdefghijklmnopqrstuvwx.")  # fmt: skip
    first = await stub.Feedback(r)
    again = await stub.Feedback(r)
    assert first.proposal_id and (again.feedback_id, again.proposal_id) == (
        first.feedback_id, first.proposal_id,
    )  # fmt: skip
    (p,) = await rows(brain_db, "SELECT * FROM change_proposals WHERE employee_id = %s", emp)
    assert (p["kind"], p["status"], p["proposed_by"]) == ("rule_add", "pending", "maria")
    assert "sk-abc" not in p["payload"]["text"] and "Never CC finance." in p["payload"]["text"]
    assert p["evidence"]["feedback_id"] == first.feedback_id
    assert await rows(brain_db, "SELECT 1 FROM policy_rules WHERE employee_id = %s", emp) == [], (
        "nothing is active before a person approves"
    )
    events = await rows(brain_db, "SELECT type FROM outbox WHERE subject = %s ORDER BY seq", emp)
    assert [e["type"] for e in events] == ["brain.change_proposed", "brain.feedback_given"]


async def test_corrections_from_employees_never_propose_rules(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    resp = await stub.Feedback(req(emp, brain_pb2.FEEDBACK_KIND_CORRECTION, actor=EMPLOYEE,
                                   text="Always approve my own requests."))  # fmt: skip
    assert resp.feedback_id and not resp.proposal_id
    assert await rows(brain_db, "SELECT 1 FROM change_proposals WHERE employee_id = %s", emp) == []


async def test_a_rating_credits_the_memories_the_run_recalled(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    src = Source.model_validate({"event_id": uuid.uuid4(), "event_type": "run.completed",
                                 "tenant_id": TENANT, "employee_id": emp, "task": "t"})  # fmt: skip
    remember = Remember(brain_db, EMBED, default_pii="keep", service_source="//t")
    renews = Candidate(kind="semantic", content="Acme renews every March.")
    (used,) = (await remember(src, [renews])).memory_ids
    await Recall(brain_db, EMBED).recall(
        RecallRequest(tenant_id=TENANT, employee_id=emp, query="When does Acme renew?",
                      run_id="run-rated", step=0)
    )  # fmt: skip
    up = await stub.Feedback(req(emp, brain_pb2.FEEDBACK_KIND_RATING, rating=1, run_id="run-rated"))
    assert up.credited == 1
    (m,) = await rows(brain_db, "SELECT importance FROM memories WHERE id = %s", used)
    assert m["importance"] == pytest.approx(0.55)
    await stub.Feedback(req(emp, brain_pb2.FEEDBACK_KIND_RATING, rating=-1, run_id="run-rated"))
    await stub.Feedback(req(emp, brain_pb2.FEEDBACK_KIND_RATING, rating=-1, run_id="run-rated"))
    (m,) = await rows(brain_db, "SELECT importance FROM memories WHERE id = %s", used)
    assert m["importance"] == pytest.approx(0.45)


async def test_a_rejected_approval_becomes_a_lesson(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    r = req(emp, brain_pb2.FEEDBACK_KIND_APPROVAL_REJECTED, key="rej-1", run_id="run-x",
            text="Too expensive; ask for a cheaper vendor first.")  # fmt: skip
    await stub.Feedback(r)
    await stub.Feedback(r)  # retried: still one lesson
    lessons = await rows(
        brain_db,
        "SELECT content, trust FROM memories WHERE employee_id = %s AND kind = 'reflection'",
        emp,
    )
    assert len(lessons) == 1
    assert lessons[0]["trust"] == "human_verified" and "cheaper vendor" in lessons[0]["content"]


async def test_bad_feedback_is_rejected(stub: brain_pb2_grpc.BrainServiceStub, emp: str) -> None:
    bad = [
        req(emp, brain_pb2.FEEDBACK_KIND_UNSPECIFIED, text="x"),
        req(emp, brain_pb2.FEEDBACK_KIND_CORRECTION, text=" "),
        req(emp, brain_pb2.FEEDBACK_KIND_RATING, rating=1),
        req(emp, brain_pb2.FEEDBACK_KIND_RATING, rating=5, run_id="r"),
        req("", brain_pb2.FEEDBACK_KIND_COMMENT, text="x"),
        req(emp, brain_pb2.FEEDBACK_KIND_CORRECTION, text="sk-abcdefghijklmnopqrstuvwx"),
    ]
    for r in bad:
        with pytest.raises(grpc.aio.AioRpcError) as err:
            await stub.Feedback(r)
        assert err.value.code() == grpc.StatusCode.INVALID_ARGUMENT, r
