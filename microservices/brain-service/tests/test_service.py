import grpc
import httpx
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from wfk_platform.db import Database

from brain_service.adapters.modelgw.embeddings import HashEmbedder
from brain_service.app import SERVING, create_app, create_grpc_server
from brain_service.recall import Recall
from brain_service.settings import Settings

UNREACHABLE = "postgresql://wfk:wfk@127.0.0.1:1/nowhere?connect_timeout=1"
LOCAL = "postgresql://wfk:wfk@127.0.0.1:7000/postgres"


async def get(app_db: Database, path: str, timeout_s: float = 0.5) -> httpx.Response:
    app = create_app(Settings(readiness_timeout_s=timeout_s), app_db)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.get(path)


async def test_liveness_does_not_need_the_database() -> None:
    r = await get(Database(UNREACHABLE), "/healthz")
    assert r.status_code == 200


async def test_not_ready_without_the_database() -> None:
    r = await get(Database(UNREACHABLE), "/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["postgres"] != "ok"


@pytest.mark.integration
async def test_ready_with_the_database() -> None:
    r = await get(Database(LOCAL), "/readyz", timeout_s=2)
    assert r.status_code == 200, r.text
    assert r.json()["checks"] == {"postgres": "ok"}


async def test_grpc_health_reports_serving() -> None:
    settings = Settings(http_host="127.0.0.1")
    server, health, port = create_grpc_server(
        settings, Recall(Database(UNREACHABLE), HashEmbedder()), port=0
    )
    await server.start()
    await health.set("", SERVING)
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            request = health_pb2.HealthCheckRequest(service="")
            # HealthStub sets its methods in __init__, which type checkers cannot see.
            resp = await stub.Check(request, timeout=2)  # pyright: ignore[reportAttributeAccessIssue]
        assert resp.status == health_pb2.HealthCheckResponse.SERVING
    finally:
        await server.stop(grace=None)
