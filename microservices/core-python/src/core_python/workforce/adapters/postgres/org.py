"""Departments and placements in workforce_db. Every change and its event commit together.

Placement changes in a tenant take one advisory lock, so two moves at once cannot build a loop
between them.
"""

from __future__ import annotations

from psycopg import errors
from psycopg.rows import DictRow

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from core_python.workforce.adapters.postgres.employees import Conflict, NotFound
from core_python.workforce.domain.org import InvalidPlacement, Member, check_placement


async def lock_org(conn: Conn, tenant: str) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('org:' || %s))", (tenant,))


async def members(conn: Conn) -> dict[str, Member]:
    cur = await conn.execute(
        "SELECT employee_id, name, title, department_id, manager_id, is_co_ceo, status"
        " FROM employees"
    )
    return {r["employee_id"]: Member(**r) for r in await cur.fetchall()}


async def place(
    conn: Conn,
    source: str,
    tenant: str,
    employee_id: str,
    *,
    title: str,
    department_id: str | None,
    manager_id: str | None,
    is_co_ceo: bool,
    department_head: bool,
) -> DictRow:
    """Place one employee inside an open transaction (hiring reuses it)."""
    await lock_org(conn, tenant)
    people = await members(conn)
    if employee_id not in people:
        raise NotFound(f"no employee {employee_id!r}")
    check_placement(people, employee_id, manager_id, is_co_ceo=is_co_ceo)
    if department_id is not None:
        cur = await conn.execute(
            "SELECT 1 FROM departments WHERE department_id = %s", (department_id,)
        )
        if await cur.fetchone() is None:
            raise InvalidPlacement(f"no department {department_id!r}")
    elif department_head:
        raise InvalidPlacement("a department head needs a department")
    cur = await conn.execute(
        "UPDATE employees SET title = %s, department_id = %s, manager_id = %s, is_co_ceo = %s,"
        " updated_at = now() WHERE employee_id = %s RETURNING *",
        (title, department_id, manager_id, is_co_ceo, employee_id),
    )
    row = await cur.fetchone()
    assert row is not None
    if department_head:
        await conn.execute(
            "UPDATE departments SET head_employee_id = %s WHERE department_id = %s",
            (employee_id, department_id),
        )
    data = {
        "employee_id": employee_id, "title": title, "department_id": department_id,
        "manager_id": manager_id, "is_co_ceo": is_co_ceo, "department_head": department_head,
    }  # fmt: skip
    await enqueue(conn, new_event("employee.placed", source, tenant, data, subject=employee_id))
    return row


class Org:
    def __init__(self, db: Database, source: str) -> None:
        self.db = db
        self.source = source

    async def create_department(
        self, tenant: str, department_id: str, name: str, *, by: str
    ) -> DictRow:
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "INSERT INTO departments (tenant_id, department_id, name, created_by)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING *",
                (tenant, department_id, name, by),
            )
            row = await cur.fetchone()
            if row is None:
                cur = await conn.execute(
                    "SELECT * FROM departments WHERE department_id = %s", (department_id,)
                )
                existing = await cur.fetchone()
                assert existing is not None
                if existing["name"] != name:
                    raise Conflict(f"department {department_id!r} exists with another name")
                return existing
            data = {"department_id": department_id, "name": name}
            await enqueue(
                conn,
                new_event(
                    "employee.department_created", self.source, tenant, data, subject=department_id
                ),
            )
            return row

    async def departments(self, tenant: str) -> list[DictRow]:
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute("SELECT * FROM departments ORDER BY name, department_id")
            return list(await cur.fetchall())

    async def place(
        self,
        tenant: str,
        employee_id: str,
        *,
        title: str,
        department_id: str | None,
        manager_id: str | None,
        is_co_ceo: bool,
        department_head: bool,
    ) -> DictRow:
        try:
            async with self.db.tenant_tx(tenant) as conn:
                return await place(
                    conn, self.source, tenant, employee_id, title=title,
                    department_id=department_id, manager_id=manager_id, is_co_ceo=is_co_ceo,
                    department_head=department_head,
                )  # fmt: skip
        except errors.UniqueViolation:  # a co-CEO named at the same moment elsewhere
            raise InvalidPlacement("there is already a co-CEO") from None

    async def chart(self, tenant: str) -> tuple[list[DictRow], list[DictRow]]:
        async with self.db.tenant_tx(tenant) as conn:
            people = await conn.execute("SELECT * FROM employees ORDER BY employee_id")
            depts = await conn.execute("SELECT * FROM departments ORDER BY name, department_id")
            return list(await people.fetchall()), list(await depts.fetchall())

    async def members(self, tenant: str) -> dict[str, Member]:
        async with self.db.tenant_tx(tenant) as conn:
            return await members(conn)
