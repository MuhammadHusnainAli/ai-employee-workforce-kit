"""wfk-migrate against the local stack (just up)."""

from pathlib import Path

import psycopg
import pytest

from wfk_platform.migrate import MigrationError, Runner, discover, ensure_database, main

pytestmark = pytest.mark.integration

DSN = "postgresql://wfk:wfk@127.0.0.1:5432/service_template_migrate_test"
TEMPLATE = Path(__file__).resolve().parents[2] / "migrations"  # this service's migrations


@pytest.fixture
def fresh_db() -> str:
    try:
        with psycopg.connect(DSN.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS service_template_migrate_test WITH (FORCE)")
    except psycopg.OperationalError as exc:
        pytest.skip(f"local stack not running (just up): {exc}")
    ensure_database(DSN)
    return DSN


def write(d: Path, name: str, body: str) -> None:
    (d / name).write_text(body, encoding="utf-8")


def tables(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as c:
        rows = c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows}


def test_template_applies_and_reverts(fresh_db: str) -> None:
    runner = Runner(fresh_db, TEMPLATE)
    assert runner.up() == [1, 2]
    assert {"outbox", "inbox", "examples", "schema_migrations"} <= tables(fresh_db)
    assert runner.up() == []  # idempotent
    assert runner.down(steps=2) == [2, 1]
    assert tables(fresh_db) == {"schema_migrations"}


def test_template_example_table_enforces_tenant_isolation(fresh_db: str) -> None:
    Runner(fresh_db, TEMPLATE).up()
    t1, t2 = "0193a7c4-0000-7000-8000-000000000001", "0193a7c4-0000-7000-8000-000000000002"
    with psycopg.connect(fresh_db) as c:
        c.execute(
            "INSERT INTO examples (tenant_id, name) VALUES (%s, 'one'), (%s, 'two')", (t1, t2)
        )
        c.commit()
        with c.transaction():
            c.execute("SET LOCAL ROLE wfk_app")
            c.execute("SELECT set_config('app.tenant_id', %s, true)", (t1,))
            names = [r[0] for r in c.execute("SELECT name FROM examples").fetchall()]
    assert names == ["one"]


def test_failed_migration_leaves_nothing_behind(fresh_db: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_ok.up.sql", "CREATE TABLE a (id int);")
    write(tmp_path, "0002_bad.up.sql", "CREATE TABLE b (id int); SELECT * FROM missing;")
    runner = Runner(fresh_db, tmp_path)
    with pytest.raises(MigrationError, match="0002_bad"):
        runner.up()
    assert "a" in tables(fresh_db)
    assert "b" not in tables(fresh_db)
    assert [(m.version, ok) for m, ok in runner.status()] == [(1, True), (2, False)]


def test_edited_applied_migration_is_refused(fresh_db: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_init.up.sql", "CREATE TABLE a (id int);")
    Runner(fresh_db, tmp_path).up()
    write(tmp_path, "0001_init.up.sql", "CREATE TABLE a (id bigint);")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        Runner(fresh_db, tmp_path).up()


def test_bad_file_names_are_rejected(tmp_path: Path) -> None:
    write(tmp_path, "init.sql", "")
    with pytest.raises(MigrationError, match="bad migration file name"):
        discover(tmp_path)


def test_cli_creates_database_and_reports(
    fresh_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(DSN.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS service_template_migrate_test WITH (FORCE)")
    assert main(["up", "--dsn", DSN, "--dir", str(TEMPLATE), "--create-db"]) == 0
    assert main(["status", "--dsn", DSN, "--dir", str(TEMPLATE)]) == 0
    out = capsys.readouterr().out
    assert "applied 2 migration(s)" in out
    assert "applied  0001 platform_outbox_inbox" in out
