"""Recall latency gate: p95 under 300 ms on a seeded dataset (brain.md section 3; plan task 3.8).

Measures the whole Recall path over real gRPC: planner, query embedding, both retrievers in
parallel, brain-core fusion and packing, the recall_log write and the Valkey write-through.
The local embedder is the hash embedder; a real embedding model adds its own latency, which
the 150 ms embedding timeout bounds.

    just bench      (prints p50 / p95 / p99)
"""

import asyncio
import random
import statistics
import time
import uuid
from collections.abc import AsyncIterator

import grpc
import psycopg
import pytest

from wfk.brain_service.v1 import brain_pb2, brain_pb2_grpc
from wfk.common.v1 import common_pb2
from wfk_platform.db import Database
from wfk_platform.migrate import Runner, ensure_database

from brain_service.adapters.cache.pinned import ValkeyPinnedCache
from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.adapters.postgres import brain as brain_repo
from brain_service.adapters.postgres.memories import vector_literal
from brain_service.app import create_server
from brain_service.domain.memory.identity import Identity
from brain_service.recall import Recall
from brain_service.settings import Settings

from ..conftest import MIGRATIONS, OTHER_TENANT, TENANT

pytestmark = pytest.mark.integration

PERF_DB = "postgresql://wfk:wfk@127.0.0.1:7000/brain_perf_test"
EMPLOYEE = "perf-employee"
P95_BUDGET_MS = 300.0
CALLS = 200
CONCURRENCY = 8

TOPICS = [
    "refund", "invoice", "contract", "renewal", "outage", "pricing", "discount", "onboarding",
    "security review", "legal hold", "migration", "quarterly report", "support ticket",
    "data export", "billing cycle", "account owner", "SLA breach", "feature request",
]  # fmt: skip
CUSTOMERS = ["Acme", "Globex", "Initech", "Umbrella", "Hooli", "Stark", "Wayne", "Tyrell"]
ACTIONS = ["approved", "escalated", "delayed", "closed", "reopened", "discussed", "documented"]


def sentence(rng: random.Random) -> str:
    return (
        f"{rng.choice(CUSTOMERS)} {rng.choice(TOPICS)} was {rng.choice(ACTIONS)} after "
        f"{rng.randint(1, 30)} days by the {rng.choice(['ops', 'finance', 'legal', 'sales'])} team"
    )


def seed(n_employee: int = 8_000, n_noise: int = 2_000) -> None:
    """Bulk-load memories with COPY, then build the indexes once (as a backfill would)."""
    with psycopg.connect(PERF_DB.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS brain_perf_test WITH (FORCE)")
    ensure_database(PERF_DB)
    Runner(PERF_DB, MIGRATIONS).up()
    rng = random.Random(42)
    embed = HashEmbedder()
    with psycopg.connect(PERF_DB) as c:
        c.execute("DROP INDEX memories_embedding")
        c.execute("DROP INDEX memories_tsv")
        cols = "id, tenant_id, employee_id, scope, kind, content, importance, trust, embedding"
        with c.cursor().copy(f"COPY memories ({cols}) FROM STDIN") as copy:
            for i in range(n_employee + n_noise):
                text = sentence(rng)
                if i < n_employee:
                    tenant, emp, scope = TENANT, EMPLOYEE, "employee"
                elif i % 3 == 0:
                    tenant, emp, scope = TENANT, None, "tenant"
                elif i % 3 == 1:
                    tenant, emp, scope = TENANT, f"other-{i % 50}", "employee"
                else:
                    tenant, emp, scope = OTHER_TENANT, EMPLOYEE, "employee"
                copy.write_row(
                    (
                        uuid.uuid4(), tenant, emp, scope,
                        rng.choice(["semantic", "semantic", "episodic", "reflection"]),
                        text, round(rng.random(), 3), rng.choice(["system", "observed"]),
                        vector_literal(embed.embed_one(text)),
                    )
                )  # fmt: skip
        c.execute(
            "CREATE INDEX memories_embedding ON memories USING hnsw (embedding vector_cosine_ops)"
        )
        c.execute("CREATE INDEX memories_tsv ON memories USING gin (tsv)")
        c.execute("ANALYZE memories")
        c.commit()


@pytest.fixture(scope="module")
async def stub() -> AsyncIterator[brain_pb2_grpc.BrainServiceStub]:
    try:
        seed()
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not running (just up): {exc}")
    db = Database(PERF_DB, tenant_role="wfk_app", max_size=20)
    await db.open()
    await brain_repo.activate_identity(
        db, TENANT, EMPLOYEE, Identity(role="Account manager for enterprise customers."), by="perf"
    )
    recall = Recall(db, HashEmbedder(), ValkeyPinnedCache("redis://127.0.0.1:7005/0"))
    server, _, port = create_server(Settings(host="127.0.0.1"), recall, port=0)
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        s = brain_pb2_grpc.BrainServiceStub(channel)
        for _ in range(10):  # warm up connections, plans and caches
            await s.Recall(request("warm up refund"))
        yield s
    await server.stop(grace=None)
    await db.close()


def request(query: str) -> brain_pb2.RecallRequest:
    return brain_pb2.RecallRequest(
        tenant=common_pb2.TenantContext(tenant_id=TENANT),
        employee_id=EMPLOYEE,
        run_id="perf",
        step=1,
        query=query,
        step_type=brain_pb2.STEP_TYPE_ACTING,
        token_budget=2_000,
    )


def queries(n: int) -> list[str]:
    rng = random.Random(7)
    return [f"{rng.choice(CUSTOMERS)} {rng.choice(TOPICS)} {rng.choice(ACTIONS)}" for _ in range(n)]


def report(label: str, ms: list[float]) -> float:
    ms = sorted(ms)
    p95 = ms[int(len(ms) * 0.95) - 1]
    print(
        f"\n{label}: n={len(ms)} p50={statistics.median(ms):.1f} ms p95={p95:.1f} ms "
        f"p99={ms[int(len(ms) * 0.99) - 1]:.1f} ms max={ms[-1]:.1f} ms"
    )
    return p95


async def timed(stub: brain_pb2_grpc.BrainServiceStub, query: str) -> float:
    started = time.perf_counter()
    resp = await stub.Recall(request(query))
    elapsed = (time.perf_counter() - started) * 1000
    assert resp.identity and len(resp.items) > 0 and not resp.degraded
    return elapsed


async def test_p95_one_caller_at_a_time(stub: brain_pb2_grpc.BrainServiceStub) -> None:
    ms = [await timed(stub, q) for q in queries(CALLS)]
    assert report("sequential", ms) < P95_BUDGET_MS


async def test_p95_with_concurrent_callers(stub: brain_pb2_grpc.BrainServiceStub) -> None:
    pending = queries(CALLS)
    ms: list[float] = []

    async def caller() -> None:
        while pending:
            ms.append(await timed(stub, pending.pop()))

    await asyncio.gather(*(caller() for _ in range(CONCURRENCY)))
    assert report(f"{CONCURRENCY} concurrent callers", ms) < P95_BUDGET_MS
