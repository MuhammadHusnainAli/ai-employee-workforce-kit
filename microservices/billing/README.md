# Billing

| | |
|---|---|
| **Scope** | global |
| **Language** | Go |
| **Database** | `billing_db` on `pg-global` |

## Purpose
Subscriptions, invoices and credit balances, fed by hourly usage rollups from every cell.

## Owns
- subscriptions
- invoices
- credit balances

## API
- subscription management
- invoice listing and download
- credit top-ups

## Events
| Direction | Subjects |
|---|---|
| In | `usage.rollup` from every cell (inbox-deduped) |
| Out | `billing.invoice_issued` · `billing.credit_low` |

## Depends on
- Tenant & Cell Directory
- payment provider (via Integration pattern)

## Scaling
Low traffic; 2–3 replicas. Rollup ingestion is batch.

## Failure behaviour
Usage keeps accumulating in cells; billing catches up from the event backlog.

## Folder layout
```
billing/
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
