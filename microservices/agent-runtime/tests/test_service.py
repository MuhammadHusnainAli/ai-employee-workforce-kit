"""The service over real gRPC: health on one port, with and without its database."""

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from wfk_platform.db import Database
from wfk_platform.health import NOT_SERVING, READINESS, SERVING

from agent_runtime.app import create_server
from agent_runtime.settings import Settings

UNREACHABLE = "postgresql://wfk:wfk@127.0.0.1:1/nowhere?connect_timeout=1"
LOCAL = "postgresql://wfk:wfk@127.0.0.1:7000/postgres"


async def check(db_url: str, service: str) -> int:
    db = Database(db_url)
    await db.open(wait=False)
    server, _, port = create_server(Settings(host="127.0.0.1", readiness_timeout_s=1.0), db, port=0)
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            request = health_pb2.HealthCheckRequest(service=service)
            # HealthStub sets its methods in __init__, which type checkers cannot see.
            resp = await stub.Check(request, timeout=5)  # pyright: ignore[reportAttributeAccessIssue]
            return resp.status
    finally:
        await server.stop(grace=None)
        await db.close()


async def test_liveness_does_not_need_the_database() -> None:
    assert await check(UNREACHABLE, "") == SERVING


async def test_not_ready_without_the_database() -> None:
    assert await check(UNREACHABLE, READINESS) == NOT_SERVING


@pytest.mark.integration
async def test_ready_with_the_database() -> None:
    assert await check(LOCAL, READINESS) == SERVING


async def test_unknown_health_service_is_not_found() -> None:
    with pytest.raises(grpc.aio.AioRpcError) as caught:
        await check(UNREACHABLE, "billing")
    assert caught.value.code() == grpc.StatusCode.NOT_FOUND
