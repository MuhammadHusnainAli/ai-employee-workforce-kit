# Usage Metering

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | `usage_db` on `pg-ledger` |

## Purpose
Counts LLM tokens, tool calls, sandbox seconds and storage per tenant and employee; enforces quotas.

## Owns
- usage_events (monthly partitions)
- hourly aggregates
- quota state

## API
- `GetUsage(tenant, period)`

## Events
| Direction | Subjects |
|---|---|
| In | `usage.*` |
| Out | `usage.rollup` (→ Billing) · `quota.exceeded` |

## Depends on
- NATS JetStream

## Scaling
KEDA on consumer lag.

## Failure behaviour
Inbox dedupe gives exactly-once counting; backlog catches up after outages.

## Folder layout
```
usage-metering/
  api/
  domain/
  adapters/
  migrations/
  tests/
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../architecture/services.md)
