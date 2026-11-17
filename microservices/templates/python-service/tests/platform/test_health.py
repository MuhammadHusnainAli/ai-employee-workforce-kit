import asyncio

import httpx
from fastapi import FastAPI

from wfk_platform import HealthRegistry, health_router


def app_with(registry: HealthRegistry) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(health_router(registry))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def ok() -> None:
    return None


async def broken() -> None:
    raise ConnectionError("db down")


async def slow() -> None:
    await asyncio.sleep(5)


async def test_ready_when_all_checks_pass() -> None:
    reg = HealthRegistry()
    reg.add("db", ok)
    async with app_with(reg) as c:
        r = await c.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"] == {"db": "ok"}


async def test_not_ready_when_a_check_fails_or_times_out() -> None:
    reg = HealthRegistry(timeout_s=0.05)
    reg.add("db", broken)
    reg.add("nats", slow)
    reg.add("cache", ok)
    async with app_with(reg) as c:
        r = await c.get("/readyz")
    assert r.status_code == 503
    checks = r.json()["checks"]
    assert checks["db"] == "error: ConnectionError"
    assert checks["nats"].startswith("timeout")
    assert checks["cache"] == "ok"


async def test_liveness_ignores_dependencies() -> None:
    reg = HealthRegistry()
    reg.add("db", broken)
    async with app_with(reg) as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
