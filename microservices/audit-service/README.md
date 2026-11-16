# Audit Service

| | |
|---|---|
| **Scope** | cell |
| **Language** | Go |
| **Database** | `audit_db` on `pg-ledger` (role has INSERT + SELECT only) |

## Purpose
Immutable record of everything humans and employees did. Answers “why did the AI do that?”.

## Owns
- audit_events (append-only, hash-chained per tenant)

## API
- `QueryAudit(filter)`
- nightly export to object storage with object lock

## Events
| Direction | Subjects |
|---|---|
| In | `*.>` (all domain events, inbox-deduped) |
| Out | none |

## Depends on
- NATS JetStream
- SeaweedFS (WORM export)

## Scaling
KEDA on consumer lag.

## Failure behaviour
Consumer lag only delays the audit view; events wait in JetStream.

## Folder layout
```
audit-service/
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
