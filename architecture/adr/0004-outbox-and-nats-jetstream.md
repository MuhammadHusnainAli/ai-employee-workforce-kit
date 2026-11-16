# ADR-0004: Transactional outbox + NATS JetStream

**Status:** Accepted

## Context
Services must tell each other about state changes without dual-write bugs (DB commit succeeds, publish fails, or
the reverse) and without consumers applying an event twice.

## Decision
- Producers write events to an `outbox` table in the same transaction as the business change. A leader-elected
  relay publishes them to NATS JetStream with `Nats-Msg-Id = event id` (broker-side dedupe).
- Consumers use durable pull consumers with explicit acks and an `inbox` table (event_id PK) written in the same
  transaction as the side effect.
- Streams use `replicas: 3` and file storage. `max_deliver` routes poison messages to a DLQ stream.
- Events are facts (CloudEvents). Multi-step processes are orchestrated by Temporal, not by event choreography.

Kafka was considered. JetStream gives the needed guarantees with far less operational weight. Because of the
outbox, switching brokers later means changing only the relay and the consumer adapter.

## Consequences
- At-least-once delivery with exactly-once effect. No event is lost if the broker is down, since the outbox
  buffers.
- Each service carries two small tables and a relay loop, provided by a shared library (infrastructure only, no
  domain logic).
