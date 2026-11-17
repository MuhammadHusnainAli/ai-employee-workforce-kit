import os
import uuid
from collections.abc import AsyncIterator
from importlib import resources

import nats
import psycopg
import pytest
from nats.aio.client import Client as NATS

from wfk_platform.db import Database

ADMIN_URL = os.environ.get("WFK_TEST_ADMIN_URL", "postgresql://wfk:wfk@127.0.0.1:7000/postgres")
TEST_DB = "brain_service_py_test"
NATS_URL = os.environ.get("WFK_TEST_NATS_URL", "nats://127.0.0.1:7003")


def _db_url(name: str) -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + name


@pytest.fixture(scope="session")
async def db() -> AsyncIterator[Database]:
    """A fresh database with the outbox and inbox tables, recreated for each test session."""
    try:
        admin = await psycopg.AsyncConnection.connect(ADMIN_URL, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"local stack not running (just up): {exc}")
    async with admin:
        await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {TEST_DB}")
    schema = resources.files("wfk_platform").joinpath("sql/outbox_inbox.sql").read_bytes()
    async with await psycopg.AsyncConnection.connect(_db_url(TEST_DB), autocommit=True) as c:
        await c.execute(schema)
    database = Database(_db_url(TEST_DB))
    await database.open()
    yield database
    await database.close()


@pytest.fixture
async def nc() -> AsyncIterator[NATS]:
    try:
        client = await nats.connect(NATS_URL, connect_timeout=2, max_reconnect_attempts=0)
    except Exception as exc:
        pytest.skip(f"NATS not running (just up): {exc}")
    yield client
    await client.drain()


@pytest.fixture
def unique() -> str:
    """A suffix that keeps streams, subjects and consumers apart between tests."""
    return uuid.uuid4().hex[:10]
