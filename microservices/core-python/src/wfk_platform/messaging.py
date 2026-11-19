"""NATS JetStream: the outbox relay (publisher side) and the inbox consumer (subscriber side).

Delivery is at least once end to end. Duplicates are removed twice:
- JetStream drops a publish whose `Nats-Msg-Id` it has seen inside the stream's duplicate
  window, so a relay that crashes after publishing but before marking the row does not
  produce a second message.
- The inbox table makes each handler run once per (consumer, event id), even if the same
  message is delivered again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from nats.aio.msg import Msg
from nats.js import JetStreamContext
from nats.js.api import StreamConfig
from nats.js.errors import NotFoundError
from pydantic import ValidationError

from wfk_platform.db import Conn, Database
from wfk_platform.outbox import CloudEvent

log = logging.getLogger(__name__)

DUPLICATE_WINDOW = timedelta(minutes=10)


async def ensure_stream(js: JetStreamContext, name: str, subjects: list[str]) -> None:
    """Create the stream if it does not exist. Idempotent."""
    try:
        await js.stream_info(name)
    except NotFoundError:
        await js.add_stream(
            StreamConfig(
                name=name,
                subjects=subjects,
                duplicate_window=DUPLICATE_WINDOW.total_seconds(),
            )
        )


class OutboxRelay:
    """Publishes committed outbox rows to JetStream, oldest first.

    Several relay replicas can run at once: rows are claimed with FOR UPDATE SKIP LOCKED.
    """

    def __init__(self, db: Database, js: JetStreamContext, *, batch_size: int = 100) -> None:
        self.db = db
        self.js = js
        self.batch_size = batch_size

    async def publish_pending(self) -> int:
        """Publish one batch. Returns how many rows were published."""
        async with self.db.tx() as conn:
            cur = await conn.execute(
                """
                SELECT seq, id, tenant_id, type, source, subject, time, traceparent, data
                FROM outbox
                WHERE published_at IS NULL
                ORDER BY seq
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (self.batch_size,),
            )
            rows = await cur.fetchall()
            published: list[int] = []
            try:
                for row in rows:
                    event = CloudEvent(
                        id=row["id"],
                        tenantid=row["tenant_id"],
                        type=row["type"],
                        source=row["source"],
                        subject=row["subject"],
                        time=row["time"],
                        traceparent=row["traceparent"],
                        data=row["data"],
                    )
                    await self.js.publish(
                        event.type, event.to_json(), headers={"Nats-Msg-Id": str(event.id)}
                    )
                    published.append(row["seq"])
            finally:
                # Mark what made it out, even if a later publish failed. The rest stay
                # pending and are retried on the next pass.
                if published:
                    await conn.execute(
                        "UPDATE outbox SET published_at = now() WHERE seq = ANY(%s)",
                        (published,),
                    )
        return len(published)


Handler = Callable[[Conn, CloudEvent], Awaitable[None]]


class InboxConsumer:
    """Pulls events from a durable JetStream consumer and runs the handler once per event.

    The handler runs inside a tenant-scoped transaction together with the inbox insert, so
    "processed" and the handler's own writes commit or roll back together.
    """

    def __init__(
        self,
        db: Database,
        js: JetStreamContext,
        *,
        consumer: str,
        subject: str,
        handler: Handler,
    ) -> None:
        self.db = db
        self.js = js
        self.consumer = consumer
        self.subject = subject
        self.handler = handler
        self._sub: JetStreamContext.PullSubscription | None = None

    async def _subscription(self) -> JetStreamContext.PullSubscription:
        if self._sub is None:
            self._sub = await self.js.pull_subscribe(self.subject, durable=self.consumer)
        return self._sub

    async def process_batch(self, batch: int = 10, wait_s: float = 1.0) -> int:
        """Fetch and handle up to `batch` messages, waiting up to `wait_s` for the first.

        Returns how many were handled (redelivered duplicates are not counted).
        """
        sub = await self._subscription()
        try:
            msgs = await sub.fetch(batch, timeout=wait_s)
        except TimeoutError:
            return 0
        handled = 0
        for msg in msgs:
            handled += await self._handle(msg)
        return handled

    async def _handle(self, msg: Msg) -> int:
        try:
            event = CloudEvent.from_json(msg.data)
        except ValidationError:
            log.error("dropping malformed event", extra={"subject": msg.subject})
            await msg.term()  # never redeliver a message that can never parse
            return 0
        try:
            async with self.db.tenant_tx(event.tenantid) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO inbox (consumer, event_id, tenant_id) VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (self.consumer, event.id, event.tenantid),
                )
                first_time = cur.rowcount == 1
                if first_time:
                    await self.handler(conn, event)
        except Exception:
            log.exception("handler failed, will be redelivered", extra={"event_id": str(event.id)})
            await msg.nak()
            return 0
        await msg.ack()
        return 1 if first_time else 0


async def run_outbox_relay(
    db: Database,
    nats_url: str,
    *,
    stream: str,
    subjects: list[str],
    stop: asyncio.Event,
    idle_s: float = 0.2,
    max_backoff_s: float = 5.0,
) -> None:
    """Publish this service's outbox to its JetStream stream until `stop` is set.

    Connects (and reconnects) on its own, so a service starts while NATS is down and catches
    up when it is back: committed events wait in the outbox, nothing is lost. Run it as a
    background task next to the service's server.
    """
    import nats

    backoff = idle_s
    while not stop.is_set():
        try:
            nc = await nats.connect(nats_url, connect_timeout=2, max_reconnect_attempts=-1)
            try:
                js = nc.jetstream()
                await ensure_stream(js, stream, subjects)
                relay = OutboxRelay(db, js)
                backoff = idle_s
                while not stop.is_set():
                    sent = await relay.publish_pending()
                    if sent < relay.batch_size:  # caught up: wait a little, or until stopped
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(stop.wait(), timeout=idle_s)
            finally:
                await nc.drain()
        except Exception as exc:  # NATS or the database is down: back off and retry
            log.warning("outbox relay paused", extra={"error": str(exc), "retry_s": backoff})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            backoff = min(backoff * 2, max_backoff_s)


async def run_inbox_consumer(
    db: Database,
    nats_url: str,
    *,
    stream: str,
    consumer: str,
    subject: str,
    handler: Handler,
    stop: asyncio.Event,
    max_backoff_s: float = 5.0,
) -> None:
    """Consume another service's stream until `stop` is set: a durable consumer on `subject`,
    each event handled once (inbox). Waits for the stream to exist, so start order between
    services does not matter. Run it as a background task next to the service's server.
    """
    import nats

    backoff = 0.2
    while not stop.is_set():
        try:
            nc = await nats.connect(nats_url, connect_timeout=2, max_reconnect_attempts=-1)
            try:
                js = nc.jetstream()
                await js.stream_info(stream)  # retried below until it exists
                inbox = InboxConsumer(db, js, consumer=consumer, subject=subject, handler=handler)
                backoff = 0.2
                while not stop.is_set():
                    await inbox.process_batch(10, wait_s=1.0)
            finally:
                await nc.drain()
        except Exception as exc:  # NATS, the stream or the database is not there yet
            log.warning(
                "inbox consumer paused",
                extra={"stream": stream, "error": str(exc), "retry_s": backoff},
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            backoff = min(backoff * 2, max_backoff_s)
