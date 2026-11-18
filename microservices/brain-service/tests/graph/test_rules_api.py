"""Rules from corrections: propose, approve (four eyes for high risk), pin, revoke (just up)."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import grpc
import pytest

from wfk.brain_service.v1 import brain_pb2, brain_pb2_grpc
from wfk.common.v1 import common_pb2
from wfk_platform.db import Database

from brain_service.adapters.cache.pinned import PinnedContext
from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.app import create_server
from brain_service.recall import Recall, RecallRequest
from brain_service.settings import Settings

from ..conftest import TENANT

pytestmark = pytest.mark.integration
EMBED = HashEmbedder()
HUMAN = common_pb2.ACTOR_KIND_HUMAN


class MemCache:
    """The pinned cache, in memory: what Recall writes through for the agent runtime."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], PinnedContext | None] = {}

    async def put(self, tenant_id: str, employee_id: str, ctx: PinnedContext | None) -> None:
        self.data[(tenant_id, employee_id)] = ctx

    async def get(self, tenant_id: str, employee_id: str) -> PinnedContext | None:
        return self.data.get((tenant_id, employee_id))


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


def ctx(who: str = "maria", kind: Any = HUMAN) -> common_pb2.TenantContext:
    return common_pb2.TenantContext(tenant_id=TENANT, actor=common_pb2.Actor(kind=kind, id=who))


async def propose(stub: brain_pb2_grpc.BrainServiceStub, emp: str, text: str) -> str:
    resp = await stub.Feedback(
        brain_pb2.FeedbackRequest(
            tenant=ctx(), idempotency_key=common_pb2.IdempotencyKey(value=uuid.uuid4().hex),
            employee_id=emp, run_id="run-1", kind=brain_pb2.FEEDBACK_KIND_CORRECTION, text=text,
        )
    )  # fmt: skip
    assert resp.proposal_id
    return resp.proposal_id


async def decide(
    stub: brain_pb2_grpc.BrainServiceStub,
    pid: str,
    approve: bool = True,
    who: str = "maria",
    kind: Any = HUMAN,
) -> brain_pb2.DecideProposalResponse:
    return await stub.DecideProposal(
        brain_pb2.DecideProposalRequest(tenant=ctx(who, kind), proposal_id=pid, approve=approve)
    )


async def code_of(call: Any) -> grpc.StatusCode:
    with pytest.raises(grpc.aio.AioRpcError) as err:
        await call
    return err.value.code()


async def recalled_rules(db: Database, emp: str, cache: MemCache | None = None) -> list[str]:
    r = await Recall(db, EMBED, cache).recall(
        RecallRequest(tenant_id=TENANT, employee_id=emp, query="anything")
    )
    return r.rules


async def test_an_approved_correction_is_pinned_in_every_recall(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    pid = await propose(stub, emp, "Reply to Ana in French.")
    pending = await stub.ListProposals(
        brain_pb2.ListProposalsRequest(tenant=ctx(), employee_id=emp, statuses=["pending"])
    )
    assert [(p.proposal_id, p.risk, p.run_id) for p in pending.proposals] == [
        (pid, "medium", "run-1")
    ]
    assert await recalled_rules(brain_db, emp) == [], "not before approval"

    resp = await decide(stub, pid)
    assert (resp.proposal.status, resp.proposal.decided_by) == ("approved", "maria")
    assert (resp.rule.text, resp.rule.status) == ("Reply to Ana in French.", "active")

    cache = MemCache()
    assert await recalled_rules(brain_db, emp, cache) == ["Reply to Ana in French."]
    pinned = cache.data[(TENANT, emp)]
    assert pinned is not None and pinned.rules == ["Reply to Ana in French."], (
        "written through for the agent runtime's fallback"
    )
    assert await code_of(decide(stub, pid)) == grpc.StatusCode.FAILED_PRECONDITION


async def test_a_rejected_proposal_never_becomes_a_rule(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    pid = await propose(stub, emp, "Use emoji in every reply.")
    resp = await decide(stub, pid, approve=False)
    assert resp.proposal.status == "rejected" and not resp.HasField("rule")
    assert await recalled_rules(brain_db, emp) == []


async def test_high_risk_rules_need_a_second_person(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    pid = await propose(stub, emp, "Never pay invoices over 500 EUR without asking.")
    assert await code_of(decide(stub, pid, who="maria")) == grpc.StatusCode.FAILED_PRECONDITION
    resp = await decide(stub, pid, who="omar")
    assert resp.rule.approved_by == "omar"


async def test_only_people_decide_and_revoke(
    stub: brain_pb2_grpc.BrainServiceStub, emp: str
) -> None:
    pid = await propose(stub, emp, "Sign emails as Sam.")
    employee = common_pb2.ACTOR_KIND_EMPLOYEE
    assert await code_of(decide(stub, pid, kind=employee)) == grpc.StatusCode.PERMISSION_DENIED
    rule = (await decide(stub, pid)).rule
    revoke = brain_pb2.RevokeRuleRequest(tenant=ctx(kind=employee), rule_id=rule.rule_id)
    assert await code_of(stub.RevokeRule(revoke)) == grpc.StatusCode.PERMISSION_DENIED


async def test_a_revoked_rule_stops_applying(
    stub: brain_pb2_grpc.BrainServiceStub, brain_db: Database, emp: str
) -> None:
    rule = (await decide(stub, await propose(stub, emp, "CC Omar on contracts."))).rule
    revoke = brain_pb2.RevokeRuleRequest(tenant=ctx(), rule_id=rule.rule_id, reason="Omar left")
    revoked = (await stub.RevokeRule(revoke)).rule
    assert (revoked.status, revoked.revoked_by) == ("revoked", "maria")
    assert await recalled_rules(brain_db, emp) == []
    assert await code_of(stub.RevokeRule(revoke)) == grpc.StatusCode.FAILED_PRECONDITION
    listed = await stub.ListRules(
        brain_pb2.ListRulesRequest(tenant=ctx(), employee_id=emp, include_revoked=True)
    )
    assert [r.status for r in listed.rules] == ["revoked"]


async def test_tenant_rules_apply_to_every_employee(brain_db: Database, emp: str) -> None:
    tenant = str(uuid.uuid4())  # its own tenant: a tenant-wide rule reaches every employee
    async with brain_db.tenant_tx(tenant) as conn:
        await conn.execute(
            "INSERT INTO policy_rules (id, tenant_id, employee_id, text, approved_by)"
            " VALUES (%s, %s, NULL, 'Never discuss salaries.', 'admin')",
            (uuid.uuid4(), tenant),
        )
    for who in (emp, f"emp-{uuid.uuid4().hex[:8]}"):
        r = await Recall(brain_db, EMBED).recall(
            RecallRequest(tenant_id=tenant, employee_id=who, query="anything")
        )
        assert r.rules == ["Never discuss salaries."]
    assert await recalled_rules(brain_db, emp) == [], "other tenants never see it"
