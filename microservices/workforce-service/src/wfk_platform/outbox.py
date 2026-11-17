"""Transactional outbox: write the event in the same transaction as the state change.

    async with db.tenant_tx(tenant_id) as conn:
        await conn.execute("UPDATE runs SET status = 'completed' ...")
        await enqueue(conn, new_event("run.completed", source, tenant_id, {"run_id": ...}))

If the transaction rolls back, the event was never written, so it is never published.
`OutboxRelay` publishes committed rows to NATS JetStream afterwards.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from opentelemetry import trace
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from wfk_platform.db import Conn
from wfk_platform.ids import uuid7

_TYPE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class CloudEvent(BaseModel):
    """CloudEvents 1.0 envelope (contracts/events/cloudevent-envelope.schema.json)."""

    specversion: Literal["1.0"] = "1.0"
    id: uuid.UUID = Field(default_factory=uuid7)
    source: str = Field(min_length=1)
    type: str
    subject: str | None = None
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    datacontenttype: Literal["application/json"] = "application/json"
    tenantid: uuid.UUID
    traceparent: str | None = None
    data: dict[str, Any]

    @field_validator("type")
    @classmethod
    def _type_is_a_subject(cls, v: str) -> str:
        if not _TYPE.match(v):
            raise ValueError(f"event type must look like a NATS subject (run.completed): {v!r}")
        return v

    def to_json(self) -> bytes:
        return self.model_dump_json(exclude_none=True).encode()

    @classmethod
    def from_json(cls, raw: bytes) -> CloudEvent:
        return cls.model_validate_json(raw)


def _current_traceparent() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{int(ctx.trace_flags):02x}"


def new_event(
    type: str,
    source: str,
    tenant_id: uuid.UUID | str,
    data: dict[str, Any],
    *,
    subject: str | None = None,
) -> CloudEvent:
    """Build an event, picking up the current trace so the trace follows it across services."""
    return CloudEvent(
        type=type,
        source=source,
        tenantid=uuid.UUID(str(tenant_id)),
        subject=subject,
        data=data,
        traceparent=_current_traceparent(),
    )


async def enqueue(conn: Conn, event: CloudEvent) -> None:
    """Write the event to the outbox inside the caller's open transaction."""
    await conn.execute(
        """
        INSERT INTO outbox (id, tenant_id, type, source, subject, time, traceparent, data)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event.id,
            event.tenantid,
            event.type,
            event.source,
            event.subject,
            event.time,
            event.traceparent,
            Jsonb(event.data),
        ),
    )
