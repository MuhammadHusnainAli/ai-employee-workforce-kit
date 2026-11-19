"""The composed service: one port carries every module's API, each module keeps its own
database, and background parts are supervised so one failing part never stops the rest."""

import asyncio

import grpc
import psycopg
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc
from psycopg import sql

from wfk_platform.health import NOT_SERVING, READINESS, SERVING

from core_python import settings as config
from core_python.app import Databases, create_server
from core_python.main import migrate, parts, supervise

UNREACHABLE = "postgresql://wfk:wfk@127.0.0.1:1/nowhere?connect_timeout=1"
PG = "postgresql://wfk:wfk@127.0.0.1:7017"


def offline_settings(**kw: object) -> config.Settings:
    urls = {f"{m}_database_url": UNREACHABLE for m in ("workforce", "runtime", "brain", "eval")}
    return config.Settings(host="127.0.0.1", readiness_timeout_s=1.0, **(urls | kw))  # type: ignore[arg-type]


async def call(port: int, method: str) -> grpc.StatusCode:
    """Call `method` with an empty message; the status says whether anything serves it."""
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        rpc = channel.unary_unary(method, request_serializer=bytes, response_deserializer=bytes)
        try:
            await rpc(b"", timeout=2)
        except grpc.aio.AioRpcError as e:
            return e.code()
        return grpc.StatusCode.OK


async def health(port: int, service: str) -> int:
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        req = health_pb2.HealthCheckRequest(service=service)
        return (await stub.Check(req, timeout=5)).status  # pyright: ignore[reportAttributeAccessIssue]


async def test_one_port_serves_every_module() -> None:
    s = offline_settings()
    dbs = Databases.from_settings(s)
    await dbs.open(wait=False)
    server, _, port = create_server(s, dbs, port=0)
    await server.start()
    try:
        assert await health(port, "") == SERVING  # liveness needs no database
        assert await health(port, READINESS) == NOT_SERVING  # no database: not ready
        for method in (
            "/wfk.workforce_service.v1.WorkforceService/ListEmployees",
            "/wfk.agent_runtime.v1.RunService/GetRun",
            "/wfk.brain_service.v1.BrainService/Recall",
        ):
            assert await call(port, method) != grpc.StatusCode.UNIMPLEMENTED, method
        assert await call(port, "/wfk.billing.v1.Billing/Charge") == grpc.StatusCode.UNIMPLEMENTED
    finally:
        await server.stop(grace=None)
        await dbs.close()


def test_modules_call_each_other_on_this_process_port() -> None:
    s = offline_settings(port=7099)
    assert config.runtime(s).brain_address == "127.0.0.1:7099"
    assert config.runtime(s).workforce_address == "127.0.0.1:7099"
    assert config.evaluation(s).brain_address == "127.0.0.1:7099"


def test_each_module_keeps_its_own_database() -> None:
    s = config.Settings()
    urls = {
        config.workforce(s).database_url,
        config.runtime(s).database_url,
        config.brain(s).database_url,
        config.evaluation(s).database_url,
    }
    assert {u.rsplit("/", 1)[1] for u in urls} == {"workforce_db", "run_db", "brain_db", "eval_db"}


def test_workers_can_be_turned_off() -> None:
    s = offline_settings()
    dbs = Databases.from_settings(s)
    assert set(parts(s, dbs)) == {
        "workforce-relay",
        "runtime-workers",
        "runtime-eval-workers",
        "brain-ingest",
        "evaluation",
    }
    assert set(parts(offline_settings(workers=False), dbs)) == {"workforce-relay"}


async def test_a_failing_part_is_restarted_until_stopped() -> None:
    stop, third = asyncio.Event(), asyncio.Event()
    starts = 0

    async def flaky(own: asyncio.Event) -> None:
        nonlocal starts
        starts += 1
        if starts < 3:
            raise RuntimeError("temporal is down")
        third.set()
        await own.wait()

    task = asyncio.create_task(supervise("flaky", flaky, stop, pause_s=0.01))
    await asyncio.wait_for(third.wait(), 2)
    assert not task.done()  # running again after two failures
    stop.set()
    await asyncio.wait_for(task, 2)
    assert starts == 3


async def test_a_part_stopping_itself_does_not_stop_the_process() -> None:
    stop, again = asyncio.Event(), asyncio.Event()
    starts = 0

    async def quits(own: asyncio.Event) -> None:
        nonlocal starts
        starts += 1
        if starts == 2:
            again.set()
        own.set()  # what each module's loop does on its way out

    task = asyncio.create_task(supervise("quits", quits, stop, pause_s=0.01))
    await asyncio.wait_for(again.wait(), 2)
    assert not stop.is_set()
    stop.set()
    await asyncio.wait_for(task, 2)


@pytest.mark.integration
def test_migrate_applies_every_module_to_its_own_database_once() -> None:
    names = {m: f"core_python_{m}_migrate_test" for m in ("workforce", "runtime", "brain", "eval")}
    try:
        admin = psycopg.connect(f"{PG}/postgres", autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not running (just up): {exc}")
    with admin:
        for name in names.values():
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
    s = config.Settings(**{f"{m}_database_url": f"{PG}/{n}" for m, n in names.items()})  # type: ignore[arg-type]
    migrate(s)
    migrate(s)  # idempotent
    tables = {
        "workforce": "employees",
        "runtime": "runs",
        "brain": "memories",
        "eval": "eval_runs",
    }
    for module, table in tables.items():
        with psycopg.connect(f"{PG}/{names[module]}") as c:
            found = {r[0] for r in c.execute("SELECT tablename FROM pg_tables").fetchall()}
        assert {table, "outbox", "inbox"} <= found, module
        others = set(tables.values()) - {table}
        assert not (others & found), f"{module} holds another module's tables"
