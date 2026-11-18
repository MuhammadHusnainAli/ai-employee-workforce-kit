"""The pinned-context cache: written through by Recall, never able to break it (just up)."""

import time
import uuid

import pytest

from wfk_platform.db import Database

from brain_service.adapters.cache.pinned import ValkeyPinnedCache, key
from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.adapters.postgres import brain as brain_repo
from brain_service.domain.memory.identity import Identity
from brain_service.recall import Recall, RecallRequest

from ..conftest import TENANT

pytestmark = pytest.mark.integration
VALKEY = "redis://127.0.0.1:7022/0"


@pytest.fixture
async def cache() -> ValkeyPinnedCache:
    c = ValkeyPinnedCache(VALKEY, timeout_s=1.0)
    try:
        await c.client.ping()
    except Exception as exc:
        pytest.skip(f"Valkey not running (just up): {exc}")
    return c


def employee() -> str:
    return f"emp-{uuid.uuid4().hex[:8]}"


async def test_recall_writes_the_pinned_context_through(
    brain_db: Database, cache: ValkeyPinnedCache
) -> None:
    emp = employee()
    await brain_repo.activate_identity(brain_db, TENANT, emp, Identity(role="Billing"), by="t")
    recall = Recall(brain_db, HashEmbedder(), cache)
    await recall.recall(RecallRequest(tenant_id=TENANT, employee_id=emp, query="anything"))

    cached = await cache.get(TENANT, emp)
    assert cached is not None
    assert (cached.version, cached.identity) == (1, Identity(role="Billing").render())
    assert 0 < await cache.client.ttl(key(TENANT, emp)) <= 24 * 3600


async def test_a_new_identity_version_reaches_the_cache_on_the_next_recall(
    brain_db: Database, cache: ValkeyPinnedCache
) -> None:
    emp = employee()
    recall = Recall(brain_db, HashEmbedder(), cache)
    await brain_repo.activate_identity(brain_db, TENANT, emp, Identity(role="Old role"), by="t")
    await recall.recall(RecallRequest(tenant_id=TENANT, employee_id=emp, query="x"))
    await brain_repo.activate_identity(brain_db, TENANT, emp, Identity(role="New role"), by="t")
    await recall.recall(RecallRequest(tenant_id=TENANT, employee_id=emp, query="x"))

    cached = await cache.get(TENANT, emp)
    assert cached is not None and cached.version == 2 and "New role" in cached.identity


async def test_no_identity_removes_a_stale_entry(
    brain_db: Database, cache: ValkeyPinnedCache
) -> None:
    emp = employee()
    await cache.client.set(key(TENANT, emp), '{"version": 9, "identity": "stale", "rules": []}')
    await Recall(brain_db, HashEmbedder(), cache).recall(
        RecallRequest(tenant_id=TENANT, employee_id=emp, query="x")
    )
    assert await cache.get(TENANT, emp) is None


async def test_a_dead_cache_never_breaks_or_slows_recall(brain_db: Database) -> None:
    dead = ValkeyPinnedCache("redis://127.0.0.1:1/0")  # nothing listens there
    emp = employee()
    await brain_repo.activate_identity(brain_db, TENANT, emp, Identity(role="Billing"), by="t")

    started = time.perf_counter()
    result = await Recall(brain_db, HashEmbedder(), dead).recall(
        RecallRequest(tenant_id=TENANT, employee_id=emp, query="x")
    )
    assert time.perf_counter() - started < 1.0
    assert result.identity == Identity(role="Billing").render()
    assert await dead.get(TENANT, emp) is None
