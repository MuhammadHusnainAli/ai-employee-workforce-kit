"""Conversations, participants and messages in collab_db. Every message and its event commit
together; a message with a key already used is returned, not stored again.

A message's seq comes from its conversation's `last_seq`, under a row lock, so each
conversation reads in one order.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from psycopg.rows import DictRow

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import enqueue, new_event

from core_python.collaboration.domain.conversations import MAX_CALL_TURNS, PERSON, pair_key
from core_python.collaboration.domain.errors import Closed, Invalid, NotAParticipant, NotFound


@dataclass
class Who:
    """Whoever speaks: its participant id, kind and the author recorded on its messages."""

    participant: str
    kind: str  # employee | person
    author: str


@dataclass
class Thread:
    conversation: DictRow
    participants: list[DictRow]
    messages: list[DictRow]


@dataclass
class CallTurn:
    """The question of one call turn, stored; and the answer when this turn was answered."""

    conversation_id: str
    transcript: list[tuple[str, str]]
    question_seq: int
    answer: str | None
    turns: int


SELECT = "SELECT * FROM conversations WHERE conversation_id = %s"
SELECT_LOCKED = SELECT + " FOR UPDATE"


class Conversations:
    def __init__(self, db: Database, source: str) -> None:
        self.db = db
        self.source = source

    # ---- inside a transaction ---------------------------------------------------------------

    @staticmethod
    async def _conversation(conn: Conn, conversation_id: str, *, lock: bool = False) -> DictRow:
        sql = SELECT_LOCKED if lock else SELECT
        row = await (await conn.execute(sql, (conversation_id,))).fetchone()
        if row is None:
            raise NotFound(f"no conversation {conversation_id}")
        return row

    @staticmethod
    async def _participants(conn: Conn, conversation_id: str) -> list[DictRow]:
        cur = await conn.execute(
            "SELECT * FROM participants WHERE conversation_id = %s ORDER BY kind, participant",
            (conversation_id,),
        )
        return list(await cur.fetchall())

    @staticmethod
    async def _by_key(conn: Conn, key: str | None) -> DictRow | None:
        if not key:
            return None
        cur = await conn.execute("SELECT * FROM messages WHERE idempotency_key = %s", (key,))
        return await cur.fetchone()

    async def _join(self, conn: Conn, tenant: str, conversation_id: Any, who: Who) -> None:
        await conn.execute(
            "INSERT INTO participants (tenant_id, conversation_id, participant, kind)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (tenant, conversation_id, who.participant, who.kind),
        )

    async def create(
        self,
        conn: Conn,
        tenant: str,
        kind: str,
        *,
        started_by: str,
        people: list[Who],
        topic: str = "",
        max_turns: int = 0,
        pair: str | None = None,
        conversation_id: str | None = None,
    ) -> DictRow:
        cur = await conn.execute(
            "INSERT INTO conversations (tenant_id, conversation_id, kind, topic, started_by,"
            " pair_key, max_turns) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                tenant,
                conversation_id or uuid.uuid4(),
                kind,
                topic[:500],
                started_by,
                pair,
                max_turns,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        for who in people:
            await self._join(conn, tenant, row["conversation_id"], who)
        return row

    async def append(
        self,
        conn: Conn,
        tenant: str,
        conversation_id: Any,
        author: str,
        author_kind: str,
        kind: str,
        text: str,
        key: str | None = None,
    ) -> DictRow:
        """Add a message to an open conversation (the caller has checked who may speak)."""
        seen = await self._by_key(conn, key)
        if seen is not None:
            return seen
        convo = await self._conversation(conn, str(conversation_id), lock=True)
        if convo["status"] != "open":
            raise Closed("this conversation is closed")
        seq = convo["last_seq"] + 1
        cur = await conn.execute(
            "INSERT INTO messages (tenant_id, conversation_id, seq, message_id, author,"
            " author_kind, kind, text, idempotency_key)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                tenant,
                conversation_id,
                seq,
                uuid.uuid4(),
                author,
                author_kind,
                kind,
                text[:20_000],
                key,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        await conn.execute(
            "UPDATE conversations SET last_seq = %s, updated_at = now() WHERE conversation_id = %s",
            (seq, conversation_id),
        )
        data = {
            "conversation_id": str(conversation_id),
            "kind": convo["kind"],
            "seq": seq,
            "author": author,
            "author_kind": author_kind,
            "message_kind": kind,
        }
        await enqueue(
            conn,
            new_event(
                "collab.message_posted", self.source, tenant, data, subject=str(conversation_id)
            ),
        )
        return row

    async def close(self, conn: Conn, tenant: str, conversation_id: Any, note: str) -> None:
        await self.append(conn, tenant, conversation_id, "system", "system", "system", note)
        await conn.execute(
            "UPDATE conversations SET status = 'closed', closed_at = now(), updated_at = now()"
            " WHERE conversation_id = %s",
            (conversation_id,),
        )

    # ---- direct messages ---------------------------------------------------------------------

    async def send(self, tenant: str, frm: Who, to: Who, text: str, key: str) -> DictRow:
        """A direct message. One conversation per pair, created on first use."""
        if frm.participant == to.participant:
            raise Invalid("a message needs someone else to go to")
        pair = pair_key(frm.participant, to.participant)
        async with self.db.tenant_tx(tenant) as conn:
            seen = await self._by_key(conn, key)
            if seen is not None:
                return seen
            cur = await conn.execute("SELECT * FROM conversations WHERE pair_key = %s", (pair,))
            convo = await cur.fetchone()
            if convo is None:
                # ON CONFLICT: a first message from each side at the same moment.
                await conn.execute(
                    "INSERT INTO conversations (tenant_id, conversation_id, kind, started_by,"
                    " pair_key) VALUES (%s, %s, 'direct', %s, %s) ON CONFLICT DO NOTHING",
                    (tenant, uuid.uuid4(), frm.participant, pair),
                )
                cur = await conn.execute("SELECT * FROM conversations WHERE pair_key = %s", (pair,))
                convo = await cur.fetchone()
                assert convo is not None
                for who in (frm, to):
                    await self._join(conn, tenant, convo["conversation_id"], who)
            return await self.append(
                conn, tenant, convo["conversation_id"], frm.author, frm.kind, "message", text, key
            )

    async def post(
        self, tenant: str, conversation_id: str, who: Who, text: str, key: str
    ) -> DictRow:
        """Say something in a conversation. The person may join any; employees only their own."""
        async with self.db.tenant_tx(tenant) as conn:
            seen = await self._by_key(conn, key)
            if seen is not None:
                return seen
            await self._conversation(conn, conversation_id)
            members = {p["participant"] for p in await self._participants(conn, conversation_id)}
            if who.participant not in members:
                if who.kind != "person":
                    raise NotAParticipant("only its participants and the person speak here")
                await self._join(conn, tenant, conversation_id, who)
            return await self.append(
                conn, tenant, conversation_id, who.author, who.kind, "message", text, key
            )

    async def inbox(self, tenant: str, participant: str, *, limit: int = 50) -> list[DictRow]:
        """Unread direct messages from others, oldest first; marks what it returns as read."""
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "SELECT m.* FROM messages m"
                " JOIN participants p ON p.conversation_id = m.conversation_id"
                "  AND p.participant = %(me)s"
                " JOIN conversations c ON c.conversation_id = m.conversation_id"
                " WHERE c.kind = 'direct' AND m.seq > p.last_read_seq AND m.author <> %(me)s"
                " ORDER BY m.created_at, m.seq LIMIT %(limit)s",
                {"me": participant, "limit": limit},
            )
            rows = list(await cur.fetchall())
            read: dict[Any, int] = {}
            for r in rows:
                read[r["conversation_id"]] = max(read.get(r["conversation_id"], 0), r["seq"])
            for conversation_id, seq in read.items():
                await conn.execute(
                    "UPDATE participants SET last_read_seq = greatest(last_read_seq, %s)"
                    " WHERE conversation_id = %s AND participant = %s",
                    (seq, conversation_id, participant),
                )
            return rows

    # ---- reading -------------------------------------------------------------------------------

    async def list(
        self, tenant: str, *, participant: str | None, kind: str | None, limit: int
    ) -> list[tuple[DictRow, list[DictRow], DictRow | None]]:
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute(
                "SELECT c.* FROM conversations c WHERE (%(k)s::text IS NULL OR c.kind = %(k)s)"
                " AND (%(p)s::text IS NULL OR EXISTS (SELECT 1 FROM participants p"
                "  WHERE p.conversation_id = c.conversation_id AND p.participant = %(p)s))"
                " ORDER BY c.updated_at DESC LIMIT %(limit)s",
                {"k": kind, "p": participant, "limit": limit},
            )
            out = []
            for convo in await cur.fetchall():
                cid = convo["conversation_id"]
                last = await conn.execute(
                    "SELECT * FROM messages WHERE conversation_id = %s AND seq = %s",
                    (cid, convo["last_seq"]),
                )
                out.append((convo, await self._participants(conn, cid), await last.fetchone()))
            return out

    async def thread(self, tenant: str, conversation_id: str, after_seq: int, limit: int) -> Thread:
        async with self.db.tenant_tx(tenant) as conn:
            convo = await self._conversation(conn, conversation_id)
            cur = await conn.execute(
                "SELECT * FROM messages WHERE conversation_id = %s AND seq > %s"
                " ORDER BY seq LIMIT %s",
                (conversation_id, after_seq, limit),
            )
            return Thread(
                convo, await self._participants(conn, conversation_id), list(await cur.fetchall())
            )

    # ---- calls ---------------------------------------------------------------------------------

    async def ask(
        self,
        tenant: str,
        caller: Who,
        callee: str,
        question: str,
        key: str,
        *,
        conversation_id: str | None,
        topic: str,
    ) -> CallTurn:
        """Store a call's question (or find it again on a retry) and what came before it."""
        async with self.db.tenant_tx(tenant) as conn:
            seen = await self._by_key(conn, key)
            if seen is not None:
                cid = seen["conversation_id"]
                answered = await self._by_key(conn, f"{key}:answer")
                convo = await self._conversation(conn, str(cid))
                return CallTurn(
                    str(cid),
                    await self._transcript(conn, cid, seen["seq"]),
                    seen["seq"],
                    answered["text"] if answered else None,
                    convo["turns"],
                )
            if conversation_id:
                convo = await self._conversation(conn, conversation_id, lock=True)
                if convo["kind"] != "call":
                    raise Invalid("that conversation is not a call")
                members = {
                    p["participant"] for p in await self._participants(conn, conversation_id)
                }
                if caller.participant not in members:
                    raise NotAParticipant("you are not on that call")
                if callee not in members:
                    raise Invalid(f"{callee} is not on that call")
            else:
                convo = await self.create(
                    conn,
                    tenant,
                    "call",
                    started_by=caller.participant,
                    topic=topic,
                    max_turns=MAX_CALL_TURNS,
                    people=[caller, Who(callee, "employee", callee)],
                )
            if convo["status"] != "open" or convo["turns"] >= convo["max_turns"]:
                raise Closed("this call has ended; start a new one")
            cid = convo["conversation_id"]
            transcript = await self._transcript(conn, cid, None)
            row = await self.append(
                conn, tenant, cid, caller.author, caller.kind, "question", question, key
            )
            return CallTurn(str(cid), transcript, row["seq"], None, convo["turns"])

    @staticmethod
    async def _transcript(
        conn: Conn, conversation_id: Any, before: int | None
    ) -> list[tuple[str, str]]:
        cur = await conn.execute(
            "SELECT kind, text FROM messages WHERE conversation_id = %s"
            " AND (%s::int IS NULL OR seq < %s) ORDER BY seq",
            (conversation_id, before, before),
        )
        return [(r["kind"], r["text"]) for r in await cur.fetchall()]

    async def answer(
        self, tenant: str, conversation_id: str, callee: str, text: str, key: str
    ) -> int:
        """Store the answer to a call turn. Returns the questions left; closes the call at 0."""
        async with self.db.tenant_tx(tenant) as conn:
            seen = await self._by_key(conn, f"{key}:answer")
            convo = await self._conversation(conn, conversation_id, lock=True)
            if seen is None:
                await self.append(
                    conn,
                    tenant,
                    conversation_id,
                    callee,
                    "employee",
                    "answer",
                    text,
                    f"{key}:answer",
                )
                cur = await conn.execute(
                    "UPDATE conversations SET turns = turns + 1 WHERE conversation_id = %s"
                    " RETURNING *",
                    (conversation_id,),
                )
                convo = await cur.fetchone()
                assert convo is not None
                if convo["turns"] >= convo["max_turns"]:
                    await self.close(
                        conn,
                        tenant,
                        conversation_id,
                        f"This call reached its {convo['max_turns']} questions and is closed.",
                    )
            return max(convo["max_turns"] - convo["turns"], 0)


PERSON_WHO = Who(PERSON, "person", PERSON)
