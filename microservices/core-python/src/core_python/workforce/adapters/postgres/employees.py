"""Employees and versions in workforce_db. Every change and its event commit together."""

from __future__ import annotations

from typing import Any

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from core_python.workforce.domain.employees import VersionSpec

SELECT = "SELECT * FROM employees WHERE employee_id = %s"
SELECT_FOR_UPDATE = "SELECT * FROM employees WHERE employee_id = %s FOR UPDATE"


class NotFound(LookupError):
    pass


class Conflict(Exception):
    pass


class Employees:
    def __init__(self, db: Database, source: str) -> None:
        self.db = db
        self.source = source

    async def _event(self, conn: Conn, tenant: str, type: str, data: dict[str, Any]) -> None:
        await enqueue(conn, new_event(type, self.source, tenant, data, subject=data["employee_id"]))

    @staticmethod
    async def _employee(conn: Conn, employee_id: str, *, lock: bool = False) -> DictRow:
        sql = SELECT_FOR_UPDATE if lock else SELECT
        row = await (await conn.execute(sql, (employee_id,))).fetchone()
        if row is None:
            raise NotFound(f"no employee {employee_id!r}")
        return row

    async def create(self, tenant: str, employee_id: str, name: str, *, by: str) -> DictRow:
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "INSERT INTO employees (tenant_id, employee_id, name, created_by)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING *",
                (tenant, employee_id, name, by),
            )
            row = await cur.fetchone()
            if row is None:  # it exists: the same create again is fine, a different one is not
                existing = await self._employee(conn, employee_id)
                if existing["name"] != name:
                    raise Conflict(f"employee {employee_id!r} exists with another name")
                return existing
            await self._event(conn, tenant, "employee.created", {"employee_id": employee_id})
            return row

    async def get(self, tenant: str, employee_id: str) -> DictRow:
        async with self.db.tenant_tx(tenant) as conn:
            return await self._employee(conn, employee_id)

    async def list(self, tenant: str) -> list[DictRow]:
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute("SELECT * FROM employees ORDER BY employee_id")
            return list(await cur.fetchall())

    async def publish(
        self, tenant: str, employee_id: str, key: str, spec: VersionSpec, *, by: str
    ) -> DictRow:
        """Store a new version and activate it. The same key returns the same version."""
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "SELECT * FROM employee_versions WHERE idempotency_key = %s", (key,)
            )
            seen = await cur.fetchone()
            if seen is not None:
                if seen["employee_id"] != employee_id:
                    raise Conflict("idempotency key already used for another employee")
                return seen
            await self._employee(conn, employee_id, lock=True)  # one publisher at a time
            cur = await conn.execute(
                "SELECT coalesce(max(version), 0) + 1 AS v FROM employee_versions"
                " WHERE employee_id = %s",
                (employee_id,),
            )
            version = (await cur.fetchone() or {"v": 1})["v"]
            cur = await conn.execute(
                "INSERT INTO employee_versions (tenant_id, employee_id, version, role,"
                " instructions, model, tool_grants, auto_approve, limits, idempotency_key,"
                " created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (
                    tenant, employee_id, version, spec.role, spec.instructions, spec.model,
                    Jsonb([g.model_dump() for g in spec.tool_grants]), spec.auto_approve,
                    Jsonb(spec.limits.model_dump(mode="json")), key, by,
                ),
            )  # fmt: skip
            row = await cur.fetchone()
            assert row is not None
            await conn.execute(
                "UPDATE employees SET active_version = %s, updated_at = now()"
                " WHERE employee_id = %s",
                (version, employee_id),
            )
            await self._event(
                conn, tenant, "employee.version_published",
                {"employee_id": employee_id, "version": version, "by": by},
            )  # fmt: skip
            return row

    async def set_active(self, tenant: str, employee_id: str, version: int) -> DictRow:
        async with self.db.tenant_tx(tenant) as conn:
            await self._employee(conn, employee_id, lock=True)
            cur = await conn.execute(
                "SELECT 1 FROM employee_versions WHERE employee_id = %s AND version = %s",
                (employee_id, version),
            )
            if await cur.fetchone() is None:
                raise NotFound(f"{employee_id!r} has no version {version}")
            cur = await conn.execute(
                "UPDATE employees SET active_version = %s, updated_at = now()"
                " WHERE employee_id = %s RETURNING *",
                (version, employee_id),
            )
            row = await cur.fetchone()
            assert row is not None
            await self._event(
                conn, tenant, "employee.version_activated",
                {"employee_id": employee_id, "version": version},
            )  # fmt: skip
            return row

    async def version(self, tenant: str, employee_id: str, version: int = 0) -> tuple[DictRow, str]:
        """(version row, employee status). Version 0 means the active one."""
        async with self.db.tenant_tx(tenant) as conn:
            employee = await self._employee(conn, employee_id)
            want = version or employee["active_version"]
            if not want:
                raise NotFound(f"{employee_id!r} has no published version")
            cur = await conn.execute(
                "SELECT * FROM employee_versions WHERE employee_id = %s AND version = %s",
                (employee_id, want),
            )
            row = await cur.fetchone()
            if row is None:
                raise NotFound(f"{employee_id!r} has no version {want}")
            return row, employee["status"]

    async def set_status(self, tenant: str, employee_id: str, status: str) -> DictRow:
        async with self.db.tenant_tx(tenant) as conn:
            current = await self._employee(conn, employee_id, lock=True)
            if current["status"] == status:
                return current
            cur = await conn.execute(
                "UPDATE employees SET status = %s, updated_at = now()"
                " WHERE employee_id = %s RETURNING *",
                (status, employee_id),
            )
            row = await cur.fetchone()
            assert row is not None
            event = "employee.paused" if status == "paused" else "employee.resumed"
            await self._event(conn, tenant, event, {"employee_id": employee_id})
            return row
