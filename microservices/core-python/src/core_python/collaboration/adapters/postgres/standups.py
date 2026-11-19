"""Standups in collab_db. A standup is also a conversation (same id): every update, every word
of the discussion and the summary is a message in it, so the person reads it like any other.

Every write is idempotent on a key derived from the standup, so a retried activity never posts
an update, a reply or a summary twice.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from core_python.collaboration.adapters.postgres.conversations import PERSON_WHO, Conversations
from core_python.collaboration.domain.errors import Closed, NotFound

OPEN = ("collecting", "discussing")


class Standups:
    def __init__(self, db: Database, source: str, conversations: Conversations) -> None:
        self.db = db
        self.source = source
        self.conversations = conversations

    async def _event(self, conn: Conn, tenant: str, type: str, data: dict[str, Any]) -> None:
        await enqueue(conn, new_event(type, self.source, tenant, data, subject=data["standup_id"]))

    @staticmethod
    async def _standup(conn: Conn, standup_id: str, *, lock: bool = False) -> DictRow:
        sql = "SELECT * FROM standups WHERE standup_id = %s"
        cur = await conn.execute(sql + " FOR UPDATE" if lock else sql, (standup_id,))
        row = await cur.fetchone()
        if row is None:
            raise NotFound(f"no standup {standup_id}")
        return row

    async def start(
        self,
        tenant: str,
        key: str,
        *,
        department_id: str,
        discussion_s: int,
        assign_next_steps: bool,
        started_by: str,
    ) -> DictRow:
        """Record the standup and its conversation; collab.standup_started starts the run."""
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute("SELECT * FROM standups WHERE idempotency_key = %s", (key,))
            seen = await cur.fetchone()
            if seen is not None:
                return seen
            convo = await self.conversations.create(
                conn,
                tenant,
                "standup",
                started_by=started_by,
                people=[PERSON_WHO],
                topic=f"Standup: {department_id or 'the whole company'}",
            )
            sid = convo["conversation_id"]
            cur = await conn.execute(
                "INSERT INTO standups (tenant_id, standup_id, department_id, started_by,"
                " discussion_s, assign_next_steps, idempotency_key)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (tenant, sid, department_id, started_by, discussion_s, assign_next_steps, key),
            )
            row = await cur.fetchone()
            assert row is not None
            await self._event(
                conn,
                tenant,
                "collab.standup_started",
                {
                    "standup_id": str(sid),
                    "department_id": department_id,
                    "discussion_s": discussion_s,
                    "assign_next_steps": assign_next_steps,
                    "started_by": started_by,
                },
            )
            return row

    async def get(self, tenant: str, standup_id: str) -> DictRow:
        async with self.db.tenant_tx(tenant) as conn:
            return await self._standup(conn, standup_id)

    async def list(self, tenant: str, limit: int) -> list[DictRow]:
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "SELECT * FROM standups ORDER BY created_at DESC LIMIT %s", (limit,)
            )
            return list(await cur.fetchall())

    async def request_end(self, tenant: str, standup_id: str) -> DictRow:
        async with self.db.tenant_tx(tenant) as conn:
            row = await self._standup(conn, standup_id, lock=True)
            if row["status"] not in OPEN:
                raise Closed(f"the standup is {row['status']}: its discussion is over")
            await self._event(
                conn, tenant, "collab.standup_end_requested", {"standup_id": standup_id}
            )
            return row

    async def since(self, tenant: str, standup_id: str, department_id: str) -> datetime | None:
        """When the last finished standup of the same scope ended."""
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "SELECT max(finished_at) AS at FROM standups WHERE department_id = %s"
                " AND status = 'done' AND standup_id <> %s",
                (department_id, standup_id),
            )
            row = await cur.fetchone()
            return row["at"] if row else None

    async def set_roster(
        self, tenant: str, standup_id: str, led_by: str, people: list[str]
    ) -> None:
        async with self.db.tenant_tx(tenant) as conn:
            await conn.execute(
                "UPDATE standups SET led_by = %s WHERE standup_id = %s", (led_by, standup_id)
            )
            for p in people:
                await conn.execute(
                    "INSERT INTO participants (tenant_id, conversation_id, participant, kind)"
                    " VALUES (%s, %s, %s, 'employee') ON CONFLICT DO NOTHING",
                    (tenant, standup_id, p),
                )

    async def add_report(
        self, tenant: str, standup_id: str, report: dict[str, Any], text: str
    ) -> None:
        eid = report["employee_id"]
        async with self.db.tenant_tx(tenant) as conn:
            await self.conversations.append(
                conn,
                tenant,
                standup_id,
                eid,
                "employee",
                "report",
                text,
                f"standup:{standup_id}:report:{eid}",
            )
            await conn.execute(
                "UPDATE standups SET reports = reports || %s WHERE standup_id = %s"
                " AND NOT reports @> %s",
                (Jsonb([report]), standup_id, Jsonb([{"employee_id": eid}])),
            )

    async def set_status(self, tenant: str, standup_id: str, status: str, note: str = "") -> None:
        async with self.db.tenant_tx(tenant) as conn:
            await conn.execute(
                "UPDATE standups SET status = %s WHERE standup_id = %s", (status, standup_id)
            )
            if note:
                await self.conversations.append(
                    conn,
                    tenant,
                    standup_id,
                    "system",
                    "system",
                    "system",
                    note,
                    f"standup:{standup_id}:status:{status}",
                )

    async def discussion(self, tenant: str, standup_id: str) -> list[DictRow]:
        """What people said in the discussion (not the updates or notes), in order."""
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "SELECT * FROM messages WHERE conversation_id = %s AND kind = 'message'"
                " ORDER BY seq",
                (standup_id,),
            )
            return list(await cur.fetchall())

    async def reply(self, tenant: str, standup_id: str, author: str, text: str, key: str) -> None:
        async with self.db.tenant_tx(tenant) as conn:
            await self.conversations.append(
                conn, tenant, standup_id, author, "employee", "message", text, key
            )

    async def finish(
        self, tenant: str, standup_id: str, summary: dict[str, Any], text: str, led_by: str
    ) -> None:
        async with self.db.tenant_tx(tenant) as conn:
            row = await self._standup(conn, standup_id, lock=True)
            if row["status"] in ("done", "failed"):
                return
            await self.conversations.append(
                conn,
                tenant,
                standup_id,
                led_by,
                "employee",
                "summary",
                text,
                f"standup:{standup_id}:summary",
            )
            await conn.execute(
                "UPDATE standups SET status = 'done', summary = %s, finished_at = now()"
                " WHERE standup_id = %s",
                (Jsonb(summary), standup_id),
            )
            await self.conversations.close(conn, tenant, standup_id, "The standup is over.")
            await self._event(
                conn,
                tenant,
                "collab.standup_finished",
                {"standup_id": standup_id, "status": "done"},
            )

    async def fail(self, tenant: str, standup_id: str, error: str) -> None:
        async with self.db.tenant_tx(tenant) as conn:
            row = await self._standup(conn, standup_id, lock=True)
            if row["status"] in ("done", "failed"):
                return
            await conn.execute(
                "UPDATE standups SET status = 'failed', error = %s, finished_at = now()"
                " WHERE standup_id = %s",
                (error[:2000], standup_id),
            )
            convo = await self.conversations._conversation(conn, standup_id)
            if convo["status"] == "open":
                await self.conversations.close(
                    conn, tenant, standup_id, f"The standup could not run: {error}"
                )
            await self._event(
                conn,
                tenant,
                "collab.standup_finished",
                {"standup_id": standup_id, "status": "failed"},
            )
