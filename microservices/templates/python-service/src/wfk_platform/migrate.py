"""wfk-migrate: plain versioned SQL migrations, the same tool for Rust and Python services.

Layout, in each service's migrations/ folder:

    0001_init.up.sql      applied by `up`
    0001_init.down.sql    applied by `down` (optional; without it the version cannot be reverted)

Rules:
- Each migration runs in its own transaction, together with its bookkeeping row, so a
  failing migration leaves nothing behind.
- A session advisory lock stops two runners (two pods starting at once) from interleaving.
- An applied migration must never change. Its checksum is stored and checked on every run;
  a mismatch stops the run. Fix forward with a new migration (expand, then contract).

    wfk-migrate up     --dsn postgresql://wfk:wfk@127.0.0.1:7000/run_db --dir migrations --create-db
    wfk-migrate down   --dsn ... --dir migrations --steps 1
    wfk-migrate status --dsn ... --dir migrations
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

LOCK_ID = 0x77666B6D  # "wfkm": one advisory lock id for every migration run
_FILE = re.compile(r"^(?P<version>\d{4,})_(?P<name>[a-z0-9_]+)\.(?P<dir>up|down)\.sql$")

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    bigint      PRIMARY KEY,
    name       text        NOT NULL,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: Path
    down: Path | None

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.up.read_bytes()).hexdigest()


def discover(directory: Path) -> list[Migration]:
    """Read migrations from a folder, checking names and pairing up/down files."""
    if not directory.is_dir():
        raise MigrationError(f"migrations folder not found: {directory}")
    ups: dict[int, tuple[str, Path]] = {}
    downs: dict[int, Path] = {}
    for path in sorted(directory.iterdir()):
        if path.suffix != ".sql":
            continue
        m = _FILE.match(path.name)
        if not m:
            raise MigrationError(f"bad migration file name: {path.name} (want 0001_name.up.sql)")
        version = int(m["version"])
        if m["dir"] == "up":
            if version in ups:
                raise MigrationError(f"two up migrations with version {version}")
            ups[version] = (m["name"], path)
        else:
            downs[version] = path
    orphans = set(downs) - set(ups)
    if orphans:
        raise MigrationError(f"down migration without an up: {sorted(orphans)}")
    return [Migration(v, n, p, downs.get(v)) for v, (n, p) in sorted(ups.items())]


def ensure_database(dsn: str) -> None:
    """Create the target database if missing (local and test use)."""
    info = conninfo_to_dict(dsn)
    name = str(info.get("dbname") or "")
    if not name:
        raise MigrationError("the DSN must name a database")
    admin = make_conninfo(dsn, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def _applied(conn: psycopg.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {int(v): str(c) for v, c in rows}


def _verify(migrations: list[Migration], applied: dict[int, str]) -> None:
    known = {m.version: m for m in migrations}
    for version, checksum in applied.items():
        m = known.get(version)
        if m is None:
            raise MigrationError(f"version {version} is applied but its file is missing")
        if m.checksum != checksum:
            raise MigrationError(
                f"{m.up.name} changed after it was applied. Never edit an applied migration; "
                "add a new one instead."
            )


class Runner:
    def __init__(self, dsn: str, directory: Path) -> None:
        self.dsn = dsn
        self.directory = directory

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.dsn, autocommit=True)
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        conn.execute(TRACKING_TABLE)
        return conn

    def up(self) -> list[int]:
        migrations = discover(self.directory)
        done: list[int] = []
        with self._connect() as conn:
            applied = _applied(conn)
            _verify(migrations, applied)
            for m in migrations:
                if m.version in applied:
                    continue
                try:
                    with conn.transaction():
                        conn.execute(m.up.read_bytes())
                        conn.execute(
                            "INSERT INTO schema_migrations (version, name, checksum)"
                            " VALUES (%s, %s, %s)",
                            (m.version, m.name, m.checksum),
                        )
                except psycopg.Error as exc:
                    raise MigrationError(f"{m.up.name} failed: {exc}") from exc
                done.append(m.version)
        return done

    def down(self, steps: int = 1) -> list[int]:
        migrations = {m.version: m for m in discover(self.directory)}
        undone: list[int] = []
        with self._connect() as conn:
            applied = _applied(conn)
            _verify(list(migrations.values()), applied)
            for version in sorted(applied, reverse=True)[:steps]:
                m = migrations[version]
                if m.down is None:
                    raise MigrationError(f"{m.up.name} has no down migration")
                try:
                    with conn.transaction():
                        conn.execute(m.down.read_bytes())
                        conn.execute("DELETE FROM schema_migrations WHERE version = %s", (version,))
                except psycopg.Error as exc:
                    raise MigrationError(f"{m.down.name} failed: {exc}") from exc
                undone.append(version)
        return undone

    def status(self) -> list[tuple[Migration, bool]]:
        migrations = discover(self.directory)
        with self._connect() as conn:
            applied = _applied(conn)
        _verify(migrations, applied)
        return [(m, m.version in applied) for m in migrations]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wfk-migrate", description="Plain versioned SQL migrations."
    )
    parser.add_argument("command", choices=["up", "down", "status"])
    parser.add_argument("--dsn", required=True, help="target database URL")
    parser.add_argument("--dir", required=True, type=Path, help="migrations folder")
    parser.add_argument("--steps", type=int, default=1, help="down: how many to revert")
    parser.add_argument("--create-db", action="store_true", help="create the database if missing")
    args = parser.parse_args(argv)

    try:
        if args.create_db:
            ensure_database(args.dsn)
        runner = Runner(args.dsn, args.dir)
        if args.command == "up":
            done = runner.up()
            print(f"applied {len(done)} migration(s): {done}" if done else "up to date")
        elif args.command == "down":
            print(f"reverted: {runner.down(args.steps)}")
        else:
            for m, applied in runner.status():
                print(f"{'applied ' if applied else 'pending '} {m.version:04d} {m.name}")
    except (MigrationError, psycopg.OperationalError) as exc:
        print(f"wfk-migrate: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
