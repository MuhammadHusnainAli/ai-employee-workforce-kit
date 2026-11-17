"""Integration tests against the local stack (just up)."""

import uuid
from typing import LiteralString

import pytest
from nats.aio.client import Client as NATS
from psycopg.rows import DictRow

from wfk_platform.db import Conn, Database
from wfk_platform.messaging import InboxConsumer, OutboxRelay, ensure_stream
from wfk_platform.outbox import CloudEvent, enqueue, new_event

pytestmark = pytest.mark.integration

T1 = uuid.UUID("0193a7c4-0000-7000-8000-000000000001")
T2 = uuid.UUID("0193a7c4-0000-7000-8000-000000000002")
SOURCE = "//cell-local/test"


class Boom(Exception):
    pass


async def outbox_rows(db: Database, event_id: uuid.UUID) -> list[DictRow]:
    async with db.tx() as conn:
        cur = await conn.execute("SELECT * FROM outbox WHERE id = %s", (event_id,))
        return await cur.fetchall()


# ---- tenancy ---------------------------------------------------------------------------


async def one(conn: Conn, query: LiteralString) -> DictRow:
    row = await (await conn.execute(query)).fetchone()
    assert row is not None
    return row


async def test_tenant_tx_sets_tenant_for_that_transaction_only(db: Database) -> None:
    async with db.tenant_tx(T1) as conn:
        assert (await one(conn, "SELECT current_setting('app.tenant_id') AS t"))["t"] == str(T1)
    async with db.tx() as conn:
        row = await one(conn, "SELECT current_setting('app.tenant_id', true) AS t")
        assert row["t"] in (None, "")


async def test_tenant_tx_rejects_non_uuid(db: Database) -> None:
    with pytest.raises(ValueError):
        async with db.tenant_tx("1 OR 1=1"):
            pass


async def test_row_level_security_hides_other_tenants(db: Database) -> None:
    async with db.tx() as conn:
        await conn.execute(
            """
            DROP TABLE IF EXISTS notes;
            CREATE TABLE notes (tenant_id uuid NOT NULL, body text NOT NULL);
            ALTER TABLE notes ENABLE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON notes
                USING (tenant_id = current_setting('app.tenant_id')::uuid)
                WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
            DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_rls_test') THEN
                    CREATE ROLE wfk_rls_test NOLOGIN;
                END IF;
            END $$;
            GRANT SELECT, INSERT ON notes TO wfk_rls_test;
            """
        )
        await conn.execute(
            "INSERT INTO notes VALUES (%s, 'tenant one'), (%s, 'tenant two')", (T1, T2)
        )
    # The superuser bypasses RLS, so act as an ordinary role the way services do.
    async with db.tenant_tx(T1) as conn:
        await conn.execute("SET LOCAL ROLE wfk_rls_test")
        cur = await conn.execute("SELECT body FROM notes")
        assert [r["body"] for r in await cur.fetchall()] == ["tenant one"]
        with pytest.raises(Exception, match="row-level security"):
            await conn.execute("INSERT INTO notes VALUES (%s, 'sneaky')", (T2,))


# ---- outbox ----------------------------------------------------------------------------


async def test_rolled_back_transaction_writes_no_event(db: Database) -> None:
    event = new_event("test.rolled_back", SOURCE, T1, {"n": 1})
    with pytest.raises(Boom):
        async with db.tenant_tx(T1) as conn:
            await enqueue(conn, event)
            raise Boom
    assert await outbox_rows(db, event.id) == []


async def test_committed_event_is_published_once(db: Database, nc: NATS, unique: str) -> None:
    js = nc.jetstream()
    subject = f"test{unique}.created"
    await ensure_stream(js, f"TEST_{unique}", [f"test{unique}.>"])
    event = new_event(subject, SOURCE, T1, {"n": 1})
    async with db.tenant_tx(T1) as conn:
        await enqueue(conn, event)

    relay = OutboxRelay(db, js)
    assert await relay.publish_pending() >= 1
    await relay.publish_pending()  # a second pass must not publish it again

    (row,) = await outbox_rows(db, event.id)
    assert row["published_at"] is not None
    info = await js.stream_info(f"TEST_{unique}")
    assert info.state.messages == 1

    sub = await js.pull_subscribe(subject, durable=f"c{unique}")
    (msg,) = await sub.fetch(1, timeout=2)
    assert msg.headers and msg.headers["Nats-Msg-Id"] == str(event.id)
    assert CloudEvent.from_json(msg.data) == event


async def test_republish_after_relay_crash_is_deduplicated(nc: NATS, unique: str) -> None:
    """A relay that published but crashed before marking the row publishes again.
    JetStream drops the second copy because the Nats-Msg-Id repeats."""
    js = nc.jetstream()
    await ensure_stream(js, f"TEST_{unique}", [f"test{unique}.>"])
    event = new_event(f"test{unique}.created", SOURCE, T1, {})
    headers = {"Nats-Msg-Id": str(event.id)}
    first = await js.publish(event.type, event.to_json(), headers=headers)
    second = await js.publish(event.type, event.to_json(), headers=headers)
    assert not first.duplicate
    assert second.duplicate
    assert (await js.stream_info(f"TEST_{unique}")).state.messages == 1


# ---- inbox -----------------------------------------------------------------------------


async def test_redelivered_event_runs_handler_once(db: Database, nc: NATS, unique: str) -> None:
    js = nc.jetstream()
    subject = f"test{unique}.created"
    await ensure_stream(js, f"TEST_{unique}", [f"test{unique}.>"])
    seen: list[uuid.UUID] = []

    async def handler(conn: Conn, event: CloudEvent) -> None:
        seen.append(event.id)

    consumer = InboxConsumer(db, js, consumer=f"c{unique}", subject=subject, handler=handler)
    event = new_event(subject, SOURCE, T1, {})
    # Same event delivered twice, as at-least-once delivery allows (distinct Nats-Msg-Ids
    # so JetStream keeps both copies and only the inbox can stop the second).
    await js.publish(subject, event.to_json(), headers={"Nats-Msg-Id": "a" + unique})
    await js.publish(subject, event.to_json(), headers={"Nats-Msg-Id": "b" + unique})

    handled = await consumer.process_batch(batch=10, wait_s=2)
    assert handled == 1
    assert seen == [event.id]


async def test_failed_handler_leaves_event_unprocessed(db: Database, nc: NATS, unique: str) -> None:
    js = nc.jetstream()
    subject = f"test{unique}.created"
    await ensure_stream(js, f"TEST_{unique}", [f"test{unique}.>"])
    calls = 0

    async def flaky(conn: Conn, event: CloudEvent) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Boom("transient")

    consumer = InboxConsumer(db, js, consumer=f"c{unique}", subject=subject, handler=flaky)
    event = new_event(subject, SOURCE, T1, {})
    await js.publish(subject, event.to_json())

    assert await consumer.process_batch(wait_s=2) == 0  # failed, nak'd, inbox rolled back
    assert await consumer.process_batch(wait_s=5) == 1  # redelivered, now succeeds
    assert calls == 2


async def test_tenant_role_keeps_rls_on_for_a_superuser_login(db: Database) -> None:
    """The local stack logs in as a superuser, which RLS ignores. tenant_role switches each
    tenant transaction to the service role, so isolation holds anyway."""
    async with db.tx() as conn:
        await conn.execute("GRANT SELECT ON notes TO wfk_rls_test")
    as_service = Database(str(db.pool.conninfo), tenant_role="wfk_rls_test")
    await as_service.open()
    try:
        async with as_service.tenant_tx(T1) as conn:
            cur = await conn.execute("SELECT body FROM notes")
            assert [r["body"] for r in await cur.fetchall()] == ["tenant one"]
        async with db.tenant_tx(T1) as conn:  # no role: the superuser sees everything
            cur = await conn.execute("SELECT count(*) AS n FROM notes")
            assert (await cur.fetchone() or {}).get("n") == 2
    finally:
        await as_service.close()
