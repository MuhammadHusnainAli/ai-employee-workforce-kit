"""Brain.Recall over real gRPC, on a migrated brain_db (just up)."""

import uuid
from collections.abc import AsyncIterator, Callable

import grpc
import pytest

from wfk.brain_service.v1 import brain_pb2, brain_pb2_grpc
from wfk.common.v1 import common_pb2
from wfk_platform.db import Database

from brain_service.adapters.modelgw.embeddings import Embedder, HashEmbedder
from brain_service.adapters.postgres import brain as brain_repo
from brain_service.adapters.postgres import memories as repo
from brain_service.app import create_grpc_server
from brain_service.domain.memory.identity import Identity
from brain_service.domain.memory.model import Memory
from brain_service.recall import Recall
from brain_service.settings import Settings

from ..conftest import OTHER_TENANT, TENANT

pytestmark = pytest.mark.integration
EMBED = HashEmbedder()
IDENTITY = Identity(
    role="Support specialist for Acme's billing questions.",
    values=["Be accurate", "Be brief"],
    hard_constraints=["Never promise a refund outside the policy"],
    escalation=["A customer threatens legal action"],
)


class FailingEmbedder:
    dims = 1024

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise ConnectionError("embedding model down")


async def serve(db: Database, embedder: Embedder) -> tuple[grpc.aio.Server, int]:
    server, _, port = create_grpc_server(
        Settings(http_host="127.0.0.1"), Recall(db, embedder), port=0
    )
    await server.start()
    return server, port


@pytest.fixture
async def stub_for(brain_db: Database) -> AsyncIterator[Callable[..., object]]:
    servers: list[grpc.aio.Server] = []
    channels: list[grpc.aio.Channel] = []

    async def make(embedder: Embedder = EMBED) -> brain_pb2_grpc.BrainServiceStub:
        server, port = await serve(brain_db, embedder)
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        servers.append(server)
        channels.append(channel)
        return brain_pb2_grpc.BrainServiceStub(channel)

    yield make
    for c in channels:
        await c.close()
    for s in servers:
        await s.stop(grace=None)


async def seed(db: Database) -> tuple[str, dict[str, Memory]]:
    emp = f"emp-{uuid.uuid4().hex[:8]}"
    await brain_repo.activate_identity(db, TENANT, emp, IDENTITY, by="test")
    mems = {
        "refund": Memory.model_validate({
            "tenant_id": TENANT, "employee_id": emp, "kind": "semantic", "trust": "human_verified",
            "content": "Acme annual plans have a 30 day refund window", "importance": 0.9,
        }),
        "episode": Memory.model_validate({
            "tenant_id": TENANT, "employee_id": emp, "kind": "episodic", "trust": "system",
            "content": "Refunded Globex within the window; the customer was happy",
        }),
        "noise": Memory.model_validate({
            "tenant_id": TENANT, "employee_id": emp, "kind": "semantic", "trust": "system",
            "content": "The office plants are watered on Fridays",
        }),
    }  # fmt: skip
    async with db.tenant_tx(TENANT) as conn:
        for m in mems.values():
            await repo.insert(conn, m, EMBED.embed_one(m.content))
    return emp, mems


def req(emp: str, query: str, **kw: object) -> brain_pb2.RecallRequest:
    return brain_pb2.RecallRequest(
        tenant=common_pb2.TenantContext(tenant_id=str(kw.pop("tenant", TENANT))),
        employee_id=emp,
        query=query,
        **kw,  # type: ignore[arg-type]
    )


async def test_recall_pins_identity_ranks_and_cites(
    stub_for: Callable[..., object], brain_db: Database
) -> None:
    emp, mems = await seed(brain_db)
    stub = await stub_for()  # type: ignore[misc]
    resp = await stub.Recall(req(emp, "refund window for Acme", run_id="run-1", step=3))

    assert resp.identity == IDENTITY.render() and resp.identity_version == 1
    assert "Never promise a refund outside the policy" in resp.identity
    ids = [i.memory_id for i in resp.items]
    assert ids[0] == str(mems["refund"].id)
    assert str(mems["episode"].id) in ids
    assert not resp.degraded and list(resp.skipped) == []
    assert all(a.score >= b.score for a, b in zip(resp.items, resp.items[1:], strict=False))

    async with brain_db.tenant_tx(TENANT) as conn:
        cur = await conn.execute("SELECT * FROM recall_log WHERE id = %s", (resp.recall_id,))
        log = await cur.fetchone()
    assert log is not None
    assert [str(i) for i in log["item_ids"]] == ids, "the log cites exactly what was returned"
    assert (log["run_id"], log["step"], log["query"]) == ("run-1", 3, "refund window for Acme")
    assert set(log["retrievers"]) == {"vector", "fulltext"}


async def test_identity_is_pinned_even_when_the_budget_is_tiny(
    stub_for: Callable[..., object], brain_db: Database
) -> None:
    emp, _ = await seed(brain_db)
    stub = await stub_for()  # type: ignore[misc]
    resp = await stub.Recall(req(emp, "refund window", token_budget=10))
    assert resp.identity == IDENTITY.render()
    assert list(resp.items) == [], "no room left for memories"


async def test_replying_uses_facts_not_episodes(
    stub_for: Callable[..., object], brain_db: Database
) -> None:
    emp, mems = await seed(brain_db)
    stub = await stub_for()  # type: ignore[misc]
    resp = await stub.Recall(
        req(emp, "refund window Globex", step_type=brain_pb2.STEP_TYPE_REPLYING)
    )
    kinds = {i.kind for i in resp.items}
    assert kinds == {"semantic"}
    assert str(mems["episode"].id) not in {i.memory_id for i in resp.items}


async def test_another_tenant_sees_nothing(
    stub_for: Callable[..., object], brain_db: Database
) -> None:
    emp, _ = await seed(brain_db)
    stub = await stub_for()  # type: ignore[misc]
    resp = await stub.Recall(req(emp, "refund window", tenant=OTHER_TENANT))
    assert resp.identity == "" and list(resp.items) == []


async def test_bad_requests_are_rejected(stub_for: Callable[..., object]) -> None:
    stub = await stub_for()  # type: ignore[misc]
    for bad in (
        req("sam", "q", tenant="acme"),
        req("", "q"),
        req("sam", "   "),
        req("sam", "q", token_budget=10**6),
    ):
        with pytest.raises(grpc.aio.AioRpcError) as caught:
            await stub.Recall(bad)
        assert caught.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_without_embeddings_recall_degrades_to_full_text(
    stub_for: Callable[..., object], brain_db: Database
) -> None:
    emp, mems = await seed(brain_db)
    stub = await stub_for(FailingEmbedder())  # type: ignore[misc]
    resp = await stub.Recall(req(emp, "refund window"))
    assert resp.degraded and list(resp.skipped) == ["vector"]
    assert str(mems["refund"].id) in {i.memory_id for i in resp.items}
    assert resp.identity == IDENTITY.render()
