"""Skills on brain_db (just up): mined once, evaluated, approved, recalled by trigger."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import grpc
import pytest
from temporalio.client import Client

from wfk.brain_service.v1 import brain_pb2, brain_pb2_grpc
from wfk.common.v1 import common_pb2
from wfk_platform.db import Database

from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.adapters.postgres.consolidate import Consolidation
from brain_service.adapters.postgres.rules import record_evaluation
from brain_service.adapters.temporal.activities import ConsolidateActivities
from brain_service.adapters.temporal.consolidate import BrainConsolidate, Employee
from brain_service.app import create_server
from brain_service.domain.learn.skills import MIN_EPISODES
from brain_service.domain.write.model import Candidate, Source
from brain_service.ingest import batch_worker, temporal_client
from brain_service.recall import Recall, RecallRequest
from brain_service.remember import Remember
from brain_service.settings import Settings

from ..conftest import OTHER_TENANT, TENANT

pytestmark = pytest.mark.integration
EMBED = HashEmbedder()
TASK = "Count the words in the Acme quarterly report"
TOOLS = ["word_count", "char_count"]


@pytest.fixture
def emp() -> str:
    return f"emp-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def stub(brain_db: Database) -> AsyncIterator[brain_pb2_grpc.BrainServiceStub]:
    server, _, port = create_server(Settings(host="127.0.0.1"), Recall(brain_db, EMBED), port=0)
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as ch:
        yield brain_pb2_grpc.BrainServiceStub(ch)
    await server.stop(None)


def remember(db: Database) -> Remember:
    return Remember(db, EMBED, default_pii="keep", service_source="//t")


def activities(db: Database) -> ConsolidateActivities:
    return ConsolidateActivities(Consolidation(db), remember(db))


async def episode(
    db: Database, emp: str, outcome: str = "success", tools: list[str] | None = None
) -> None:
    s = Source.model_validate({"event_id": uuid.uuid4(), "event_type": "run.completed",
                               "tenant_id": TENANT, "employee_id": emp, "run_id": "r",
                               "task": TASK, "outcome": outcome,
                               "tools": TOOLS if tools is None else tools})  # fmt: skip
    ep = Candidate(kind="episodic", content=f"Task: {TASK}\nOutcome: {outcome}.", confidence=1.0)
    await remember(db)(s, [ep])


async def rows(db: Database, sql: str, *args: Any, tenant: str = TENANT) -> list[dict[str, Any]]:
    async with db.tenant_tx(tenant) as conn:
        cur = await conn.execute(sql, args)  # type: ignore[arg-type]
        return [dict(r) for r in await cur.fetchall()]


async def mine(db: Database, emp: str) -> int:
    return await activities(db).mine_skills(Employee(tenant_id=TENANT, employee_id=emp))


async def mined(db: Database, emp: str) -> str:
    """MIN_EPISODES successes with one tool sequence, mined: the skill proposal's id."""
    for _ in range(MIN_EPISODES):
        await episode(db, emp)
    assert await mine(db, emp) == 1
    (p,) = await rows(db, "SELECT id FROM change_proposals WHERE employee_id = %s", emp)
    return str(p["id"])


def ctx(who: str = "maria", tenant: str = TENANT) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(
        tenant_id=tenant, actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_HUMAN, id=who)
    )


async def recall(db: Database, emp: str, query: str, candidate: str | None = None) -> list[str]:
    r = await Recall(db, EMBED).recall(
        RecallRequest(tenant_id=TENANT, employee_id=emp, query=query,
                      candidate_proposal_id=candidate)
    )  # fmt: skip
    return [s.name for s in r.skills]


async def test_the_consolidation_pass_proposes_a_skill_once(brain_db: Database, emp: str) -> None:
    try:
        client: Client = await temporal_client(Settings())
    except Exception as exc:
        pytest.skip(f"Temporal not running (just up): {exc}")
    for _ in range(MIN_EPISODES):
        await episode(brain_db, emp)
    await episode(brain_db, emp, outcome="failure")  # failures are never evidence
    await episode(brain_db, emp, tools=[])  # nor runs without tools

    async def a_pass() -> dict[str, int]:
        queue = f"brain-batch-test-{uuid.uuid4().hex[:8]}"
        async with batch_worker(client, activities(brain_db), queue):
            out = await client.execute_workflow(
                BrainConsolidate.run, Employee(tenant_id=TENANT, employee_id=emp),
                id=f"test-skills-{uuid.uuid4().hex[:8]}", task_queue=queue,
            )  # fmt: skip
        return out.counts

    assert (await a_pass())["skills_proposed"] == 1
    (p,) = await rows(brain_db, "SELECT * FROM change_proposals WHERE employee_id = %s", emp)
    assert (p["kind"], p["risk"], p["status"], p["evidence"]["count"]) == (
        "skill_add", "low", "pending", MIN_EPISODES)  # fmt: skip
    assert p["payload"]["tools"] == TOOLS
    (s,) = await rows(brain_db, "SELECT * FROM skills WHERE employee_id = %s", emp)
    assert (s["status"], s["tools"], s["proposal_id"]) == ("proposed", TOOLS, p["id"])
    events = await rows(
        brain_db, "SELECT data FROM outbox WHERE type = 'brain.change_proposed'"
        " AND data->>'proposal_id' = %s", str(p["id"]),
    )  # fmt: skip
    assert len(events) == 1

    await episode(brain_db, emp)  # one more success: the same pattern, nothing new
    assert (await a_pass())["skills_proposed"] == 0
    assert len(await rows(brain_db, "SELECT 1 FROM skills WHERE employee_id = %s", emp)) == 1


async def test_four_successes_are_not_yet_a_skill(brain_db: Database, emp: str) -> None:
    for _ in range(MIN_EPISODES - 1):
        await episode(brain_db, emp)
    assert await mine(brain_db, emp) == 0


async def test_an_approved_skill_is_recalled_by_its_trigger(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    pid = await mined(brain_db, emp)
    assert await recall(brain_db, emp, TASK) == [], "a proposed skill is not recalled"

    decide = brain_pb2.DecideProposalRequest(tenant=ctx(), proposal_id=pid, approve=True)
    with pytest.raises(grpc.aio.AioRpcError) as err:
        await stub.DecideProposal(decide)
    assert err.value.code() == grpc.StatusCode.FAILED_PRECONDITION, "not evaluated yet"
    async with brain_db.tenant_tx(TENANT) as conn:
        await record_evaluation(conn, {"proposal_id": pid, "passed": True})
    resp = await stub.DecideProposal(decide)
    assert (resp.proposal.status, resp.proposal.kind) == ("approved", "skill_add")
    assert (resp.skill.status, list(resp.skill.tools)) == ("active", TOOLS)
    assert resp.proposal.evidence_count == MIN_EPISODES
    assert not resp.HasField("rule")

    r = await stub.Recall(brain_pb2.RecallRequest(tenant=ctx(), employee_id=emp, query=TASK))
    assert [(s.name, list(s.tools)) for s in r.skills] == [("word_count then char_count", TOOLS)]
    assert "1. Use word_count." in r.skills[0].procedure
    assert await recall(brain_db, emp, "Book a table for four at the harbour") == []
    (s,) = await rows(brain_db, "SELECT use_count FROM skills WHERE employee_id = %s", emp)
    assert s["use_count"] == 1, "only the matching recall counts as a use"

    listed = await stub.ListSkills(
        brain_pb2.ListSkillsRequest(tenant=ctx(), employee_id=emp, statuses=["active"])
    )
    assert [(s.status, len(s.evidence), s.approved_by) for s in listed.skills] == [
        ("active", MIN_EPISODES, "maria")
    ]
    other = await stub.ListSkills(
        brain_pb2.ListSkillsRequest(tenant=ctx(tenant=OTHER_TENANT), employee_id=emp)
    )
    assert list(other.skills) == [], "another tenant sees nothing"


async def test_a_rejected_skill_is_never_recalled_or_proposed_again(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    pid = await mined(brain_db, emp)
    resp = await stub.DecideProposal(
        brain_pb2.DecideProposalRequest(tenant=ctx(), proposal_id=pid, approve=False)
    )
    assert (resp.proposal.status, resp.skill.status) == ("rejected", "rejected")
    assert await recall(brain_db, emp, TASK) == []
    assert await recall(brain_db, emp, TASK, candidate=pid) == []
    await episode(brain_db, emp)
    assert await mine(brain_db, emp) == 0


async def test_an_evaluation_run_sees_the_proposed_skill_only_for_its_employee(
    brain_db: Database, emp: str
) -> None:
    pid = await mined(brain_db, emp)
    assert await recall(brain_db, emp, "anything at all", candidate=pid) == [
        "word_count then char_count"
    ]
    assert await recall(brain_db, "someone-else", "anything", candidate=pid) == []
