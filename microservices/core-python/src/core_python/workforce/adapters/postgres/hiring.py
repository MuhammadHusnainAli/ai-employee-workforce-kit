"""Hiring in workforce_db: direct hires by a person, and requests a person decides.

A hire is one transaction: the employee, its first version and its place, with their events. If
any part is refused (a manager that does not exist, a second co-CEO), nothing is created.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import errors
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from core_python.workforce.adapters.postgres.employees import (
    Conflict,
    Employees,
    NotFound,
    Precondition,
    insert_version,
)
from core_python.workforce.adapters.postgres.org import place
from core_python.workforce.domain.hiring import Hire
from core_python.workforce.domain.org import InvalidPlacement


class Hiring:
    def __init__(self, db: Database, source: str) -> None:
        self.db = db
        self.source = source

    async def _event(self, conn: Conn, tenant: str, type: str, data: dict[str, Any]) -> None:
        await enqueue(conn, new_event(type, self.source, tenant, data, subject=data["request_id"]))

    async def _hire(
        self, conn: Conn, tenant: str, key: str, hire: Hire, *, by: str
    ) -> tuple[DictRow, DictRow]:
        cur = await conn.execute(
            "SELECT * FROM employee_versions WHERE idempotency_key = %s", (key,)
        )
        seen = await cur.fetchone()
        if seen is not None:
            if seen["employee_id"] != hire.employee_id:
                raise Conflict("idempotency key already used for another employee")
            return await Employees._employee(conn, hire.employee_id), seen
        cur = await conn.execute(
            "INSERT INTO employees (tenant_id, employee_id, name, created_by)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING *",
            (tenant, hire.employee_id, hire.name, by),
        )
        if await cur.fetchone() is None:
            raise Conflict(f"employee {hire.employee_id!r} already exists")
        await enqueue(
            conn,
            new_event(
                "employee.created",
                self.source,
                tenant,
                {"employee_id": hire.employee_id},
                subject=hire.employee_id,
            ),
        )
        version = await insert_version(
            conn, self.source, tenant, hire.employee_id, key, hire.version(), by=by
        )
        employee = await place(
            conn,
            self.source,
            tenant,
            hire.employee_id,
            title=hire.title,
            department_id=hire.department_id,
            manager_id=hire.manager_id,
            is_co_ceo=hire.is_co_ceo,
            department_head=hire.department_head,
        )
        return employee, version

    async def hire(self, tenant: str, key: str, hire: Hire, *, by: str) -> tuple[DictRow, DictRow]:
        try:
            async with self.db.tenant_tx(tenant) as conn:
                return await self._hire(conn, tenant, key, hire, by=by)
        except errors.UniqueViolation:
            raise InvalidPlacement("there is already a co-CEO") from None

    async def request(
        self, tenant: str, key: str, hire: Hire, reason: str, *, by: str, by_kind: str
    ) -> DictRow:
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "INSERT INTO hire_requests (tenant_id, request_id, requested_by,"
                " requested_by_kind, hire, reason, idempotency_key)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING *",
                (
                    tenant,
                    uuid.uuid4(),
                    by,
                    by_kind,
                    Jsonb(hire.model_dump(mode="json")),
                    reason,
                    key,
                ),
            )
            row = await cur.fetchone()
            if row is None:
                cur = await conn.execute(
                    "SELECT * FROM hire_requests WHERE idempotency_key = %s", (key,)
                )
                existing = await cur.fetchone()
                assert existing is not None
                if existing["hire"]["employee_id"] != hire.employee_id:
                    raise Conflict("idempotency key already used for another hire request")
                return existing
            await self._event(
                conn,
                tenant,
                "employee.hire_requested",
                {
                    "request_id": str(row["request_id"]),
                    "employee_id": hire.employee_id,
                    "requested_by": by,
                    "reason": reason,
                },
            )
            return row

    async def list(self, tenant: str, status: str | None) -> list[DictRow]:
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "SELECT * FROM hire_requests WHERE (%(s)s::text IS NULL OR status = %(s)s)"
                " ORDER BY created_at DESC LIMIT 200",
                {"s": status},
            )
            return list(await cur.fetchall())

    async def decide(
        self, tenant: str, request_id: str, approved: bool, note: str, *, by: str
    ) -> tuple[DictRow, DictRow | None]:
        """Approve (hire) or reject a pending request. A hire that no longer fits raises and
        leaves the request pending, so the person can fix the chart and approve again."""
        try:
            async with self.db.tenant_tx(tenant) as conn:
                cur = await conn.execute(
                    "SELECT * FROM hire_requests WHERE request_id = %s FOR UPDATE", (request_id,)
                )
                req = await cur.fetchone()
                if req is None:
                    raise NotFound(f"no hire request {request_id}")
                if req["status"] != "pending":
                    raise Precondition(f"hire request is already {req['status']}")
                employee = None
                if approved:
                    hire = Hire.model_validate(req["hire"])
                    employee, _ = await self._hire(
                        conn, tenant, f"hire-request:{request_id}", hire, by=by
                    )
                cur = await conn.execute(
                    "UPDATE hire_requests SET status = %s, decided_by = %s, decision_note = %s,"
                    " decided_at = now() WHERE request_id = %s RETURNING *",
                    ("approved" if approved else "rejected", by, note, request_id),
                )
                row = await cur.fetchone()
                assert row is not None
                await self._event(
                    conn,
                    tenant,
                    "employee.hire_decided",
                    {
                        "request_id": request_id,
                        "employee_id": req["hire"]["employee_id"],
                        "approved": approved,
                        "by": by,
                    },
                )
                return row, employee
        except errors.UniqueViolation:
            raise InvalidPlacement("there is already a co-CEO") from None
