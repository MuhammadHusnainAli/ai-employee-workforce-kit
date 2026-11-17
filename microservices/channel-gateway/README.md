# Channel Gateway

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | `channel_db` on `pg-app` |

## Purpose
Inbound and outbound conversation channels: email, Slack, Microsoft Teams, Google Chat and webhooks. Turns external messages into tasks and task replies into messages.

## Owns
- channel bindings (inbox / channel → employee)
- inbound message log
- delivery receipts

## API
- webhook endpoints `/hooks/{tenant_slug}/…` (signature-verified)
- `DeliverMessage` (called by run activities)

## Events
| Direction | Subjects |
|---|---|
| In | `task.state_changed`, `approval.requested` (notifications) |
| Out | `channel.message_received` · `channel.message_delivered` |

## Depends on
- Task Service (`CreateTask`)
- Integration Service (outbound sends)

## Scaling
HPA on RPS, ≥ 3 replicas.

## Failure behaviour
Provider message ID has a unique index: duplicate webhooks are no-ops. Outbound sends are idempotent activities.

## Folder layout
```
channel-gateway/
  api/
  domain/
  adapters/email/
  adapters/slack/
  adapters/teams/
  adapters/google-chat/
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
