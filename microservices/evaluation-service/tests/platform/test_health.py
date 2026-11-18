import asyncio

from wfk_platform.health import (
    LIVENESS,
    NOT_SERVING,
    READINESS,
    SERVING,
    GrpcHealth,
    HealthRegistry,
)


async def ok() -> None:
    return None


async def broken() -> None:
    raise ConnectionError("db down")


async def slow() -> None:
    await asyncio.sleep(5)


async def test_ready_when_all_checks_pass() -> None:
    reg = HealthRegistry()
    reg.add("db", ok)
    assert await reg.readiness() == (True, {"db": "ok"})
    assert await GrpcHealth(reg).status(READINESS) == SERVING


async def test_not_ready_when_a_check_fails_or_times_out() -> None:
    reg = HealthRegistry(timeout_s=0.05)
    reg.add("db", broken)
    reg.add("nats", slow)
    reg.add("cache", ok)
    ready, checks = await reg.readiness()
    assert not ready
    assert checks["db"] == "error: ConnectionError"
    assert checks["nats"].startswith("timeout")
    assert checks["cache"] == "ok"
    assert await GrpcHealth(reg).status(READINESS) == NOT_SERVING


async def test_liveness_ignores_dependencies() -> None:
    reg = HealthRegistry()
    reg.add("db", broken)
    assert await GrpcHealth(reg).status(LIVENESS) == SERVING


async def test_shutdown_reports_not_serving_so_traffic_drains() -> None:
    health = GrpcHealth(HealthRegistry())
    health.shutdown()
    assert await health.status(LIVENESS) == NOT_SERVING
    assert await health.status(READINESS) == NOT_SERVING


async def test_unknown_service_has_no_status() -> None:
    assert await GrpcHealth(HealthRegistry()).status("billing") is None
