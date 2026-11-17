"""The parallel retrieval runner, the embedder and the memory model (no database)."""

import asyncio
import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from brain_service.adapters.modelgw.embeddings import DIMS, HashEmbedder
from brain_service.domain.memory.model import Memory
from brain_service.domain.recall.retrieval import RecallQuery, retrieve

T = uuid.UUID("0193a7c4-0000-7000-8000-000000000001")
NOW = datetime(2026, 11, 17, tzinfo=UTC)


def memory(content: str) -> Memory:
    return Memory(tenant_id=T, employee_id="sam", kind="semantic", content=content, trust="system")


def query() -> RecallQuery:
    return RecallQuery(
        tenant_id=str(T),
        employee_id="sam",
        text="x",
        embedding=[0.0] * DIMS,
        kinds=("semantic",),
        as_of=NOW,
    )


async def test_slow_and_failing_retrievers_are_skipped_not_waited_for() -> None:
    fast_hit = memory("fast")

    async def fast(q: RecallQuery) -> list[Memory]:
        return [fast_hit]

    async def slow(q: RecallQuery) -> list[Memory]:
        await asyncio.sleep(5)
        return [memory("too late")]

    async def broken(q: RecallQuery) -> list[Memory]:
        raise ConnectionError("store down")

    started = asyncio.get_running_loop().time()
    out = await retrieve(query(), {"vector": fast, "graph": slow, "fulltext": broken})
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.5, "recall never waits for the slowest store"
    assert out.rankings["vector"] == [fast_hit]
    assert out.rankings["graph"] == [] and out.rankings["fulltext"] == []
    assert out.reports["graph"].skipped == "timeout"
    assert out.reports["fulltext"].skipped == "error: ConnectionError"
    assert out.reports["vector"].skipped is None and out.reports["vector"].hits == 1
    assert out.degraded


async def test_no_skips_means_not_degraded() -> None:
    async def ok(q: RecallQuery) -> list[Memory]:
        return []

    assert not (await retrieve(query(), {"vector": ok})).degraded


def test_hash_embedder_is_deterministic_normalised_and_lexical() -> None:
    e = HashEmbedder()
    a = e.embed_one("refund policy for enterprise customers")
    assert len(a) == DIMS
    assert a == e.embed_one("refund policy for enterprise customers")
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-9)

    def cos(x: list[float], y: list[float]) -> float:
        return sum(p * q for p, q in zip(x, y, strict=True))

    near = e.embed_one("what is the refund policy")
    far = e.embed_one("the office coffee machine is broken")
    assert cos(a, near) > cos(a, far)


def test_memory_invariants() -> None:
    with pytest.raises(ValidationError):
        Memory(tenant_id=T, employee_id=None, kind="semantic", content="x", trust="system")
    with pytest.raises(ValidationError):
        Memory(
            tenant_id=T,
            employee_id="sam",
            kind="semantic",
            content="x",
            trust="system",
            valid_from=NOW,
            valid_to=NOW - timedelta(days=1),
        )
    with pytest.raises(ValidationError):
        Memory(tenant_id=T, employee_id="sam", kind="semantic", content="x", trust="gossip")  # type: ignore[arg-type]
    tenant_fact = Memory(
        tenant_id=T, employee_id=None, scope="tenant", kind="semantic", content="x", trust="system"
    )
    assert tenant_fact.scope == "tenant"
    old = memory("x").model_copy(update={"recorded_at": NOW - timedelta(days=3)})
    assert old.age_days(NOW) == pytest.approx(3)
