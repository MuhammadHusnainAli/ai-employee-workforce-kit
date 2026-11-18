"""Fixtures shared by this service's tests."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from wfk_platform.db import Database
from wfk_platform.migrate import Runner, ensure_database

TENANT = "0193a7c4-0000-7000-8000-000000000001"
OTHER_TENANT = "0193a7c4-0000-7000-8000-000000000002"
RUN_DB = "postgresql://wfk:wfk@127.0.0.1:7017/agent_runtime_test"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture(scope="session")
async def run_db() -> AsyncIterator[Database]:
    """A migrated run_db for the whole session (just up)."""
    import psycopg

    try:
        with psycopg.connect(RUN_DB.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS agent_runtime_test WITH (FORCE)")
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not running (just up): {exc}")
    ensure_database(RUN_DB)
    Runner(RUN_DB, MIGRATIONS).up()
    db = Database(RUN_DB, tenant_role="wfk_app")  # as the service runs: under RLS
    await db.open()
    yield db
    await db.close()
