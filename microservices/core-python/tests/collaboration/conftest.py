"""Fixtures shared by the collaboration module's tests."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from wfk_platform.db import Database
from wfk_platform.migrate import Runner, ensure_database

TENANT = "0193a7c4-0000-7000-8000-0000000000c1"
OTHER_TENANT = "0193a7c4-0000-7000-8000-0000000000c2"
COLLAB_DB = "postgresql://wfk:wfk@127.0.0.1:7017/collaboration_test"
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations" / "collaboration"


@pytest.fixture(scope="session")
async def collab_db() -> AsyncIterator[Database]:
    """A migrated collab_db for the whole session (just up), used under RLS like the service."""
    import psycopg

    try:
        with psycopg.connect(COLLAB_DB.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS collaboration_test WITH (FORCE)")
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not running (just up): {exc}")
    ensure_database(COLLAB_DB)
    Runner(COLLAB_DB, MIGRATIONS).up()
    db = Database(COLLAB_DB, tenant_role="wfk_app")
    await db.open()
    yield db
    await db.close()
