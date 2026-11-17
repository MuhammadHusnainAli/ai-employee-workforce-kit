"""wfk-migrate against the local stack (just up)."""

from pathlib import Path

import psycopg
import pytest

from wfk_platform.migrate import MigrationError, Runner, discover, ensure_database, main

pytestmark = pytest.mark.integration

DSN = "postgresql://wfk:wfk@127.0.0.1:5432/brain_service_migrate_test"
TEMPLATE = Path(__file__).resolve().parents[2] / "migrations"  # this service's migrations


@pytest.fixture
def fresh_db() -> str:
    try:
        with psycopg.connect(DSN.rsplit("/", 1)[0] + "/postgres", autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS brain_service_migrate_test WITH (FORCE)")
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


BRAIN_TABLES = {"brain_versions", "memories", "episodes", "recall_log"}
T1, T2 = "0193a7c4-0000-7000-8000-000000000001", "0193a7c4-0000-7000-8000-000000000002"


def test_brain_db_applies_and_reverts(fresh_db: str) -> None:
    runner = Runner(fresh_db, TEMPLATE)
    assert runner.up() == [1, 2]
    assert BRAIN_TABLES | {"outbox", "inbox"} <= tables(fresh_db)
    assert runner.up() == []
    assert runner.down(steps=2) == [2, 1]
    assert tables(fresh_db) == {"schema_migrations"}


def test_retrieval_indexes_exist(fresh_db: str) -> None:
    Runner(fresh_db, TEMPLATE).up()
    with psycopg.connect(fresh_db) as c:
        defs = {
            r[0]: r[1] for r in c.execute("SELECT indexname, indexdef FROM pg_indexes").fetchall()
        }
    assert "USING hnsw (embedding vector_cosine_ops)" in defs["memories_embedding"]
    assert "USING gin (tsv)" in defs["memories_tsv"]


def insert_memory(c: psycopg.Connection, tenant: str, content: str, **kw: object) -> None:
    c.execute(
        "INSERT INTO memories (id, tenant_id, employee_id, scope, kind, content, trust)"
        " VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s)",
        (tenant, kw.get("employee", "sam"), kw.get("scope", "employee"),
         kw.get("kind", "semantic"), content, kw.get("trust", "system")),
    )  # fmt: skip


def test_every_brain_table_enforces_tenant_isolation(fresh_db: str) -> None:
    Runner(fresh_db, TEMPLATE).up()
    with psycopg.connect(fresh_db) as c:
        for tenant in (T1, T2):
            insert_memory(c, tenant, f"fact of {tenant}")
            c.execute(
                "INSERT INTO brain_versions (tenant_id, employee_id, version, status, identity,"
                " identity_text, created_by) VALUES (%s, 'sam', 1, 'active', '{}', 'x', 'test')",
                (tenant,),
            )
            c.execute(
                "INSERT INTO recall_log (id, tenant_id, employee_id, query)"
                " VALUES (gen_random_uuid(), %s, 'sam', 'q')",
                (tenant,),
            )
        c.commit()
        with c.transaction():
            c.execute("SET LOCAL ROLE wfk_app")
            c.execute("SELECT set_config('app.tenant_id', %s, true)", (T1,))
            for table in ("memories", "brain_versions", "recall_log"):
                (n,) = c.execute(f"SELECT count(*) FROM {table}").fetchone()  # type: ignore[misc]
                assert n == 1, table
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
                insert_memory(c, T2, "sneaky")


def test_one_active_identity_version_per_employee(fresh_db: str) -> None:
    Runner(fresh_db, TEMPLATE).up()
    with psycopg.connect(fresh_db) as c:
        sql = (
            "INSERT INTO brain_versions (tenant_id, employee_id, version, status, identity,"
            " identity_text, created_by) VALUES (%s, 'sam', %s, %s, '{}', 'x', 'test')"
        )
        c.execute(sql, (T1, 1, "active"))
        c.execute(sql, (T1, 2, "candidate"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(sql, (T1, 3, "active"))


def test_memory_rules_are_enforced(fresh_db: str) -> None:
    Runner(fresh_db, TEMPLATE).up()
    with psycopg.connect(fresh_db, autocommit=True) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_memory(c, T1, "x", trust="rumour")
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_memory(c, T1, "x", employee=None)  # employee scope needs an employee
        insert_memory(c, T1, "company holiday on 1 May", employee=None, scope="tenant")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE memories SET valid_to = valid_from - interval '1 day'")
        (tsv,) = c.execute("SELECT tsv::text FROM memories").fetchone()  # type: ignore[misc]
        assert "holiday" in tsv


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
        c.execute("DROP DATABASE IF EXISTS brain_service_migrate_test WITH (FORCE)")
    assert main(["up", "--dsn", DSN, "--dir", str(TEMPLATE), "--create-db"]) == 0
    assert main(["status", "--dsn", DSN, "--dir", str(TEMPLATE)]) == 0
    out = capsys.readouterr().out
    assert "applied 2 migration(s)" in out
    assert "applied  0001 platform_outbox_inbox" in out
