"""Fixtures shared by this service's tests."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from wfk_platform.db import Database
from wfk_platform.migrate import Runner, ensure_database

TENANT = "0193a7c4-0000-7000-8000-000000000001"
OTHER_TENANT = "0193a7c4-0000-7000-8000-000000000002"
WORKFORCE_DB = "postgresql://wfk:wfk@127.0.0.1:7000/workforce_service_test"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture(scope="session")
async def workforce_db() -> AsyncIterator[Database]:
    """A migrated workforce_db for the whole session (just up), used under RLS like the service."""
    import psycopg

    try:
        with psycopg.connect(WORKFORCE_DB.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS workforce_service_test WITH (FORCE)")
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not running (just up): {exc}")
    ensure_database(WORKFORCE_DB)
    Runner(WORKFORCE_DB, MIGRATIONS).up()
    db = Database(WORKFORCE_DB, tenant_role="wfk_app")
    await db.open()
    yield db
    await db.close()
