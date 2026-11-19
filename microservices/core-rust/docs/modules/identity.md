# Identity & Accounts

> **Planned module of core-rust** (spec only, not built yet). Written as the `identity-accounts` service; under [ADR-0017](../../../../architecture/adr/0017-two-backend-services.md) it is built as a module inside core-rust, with its own database, migrations and contracts, and no port or container of its own.

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | `identity_db` on `pg-app` (secrets only in OpenBao) |

## Purpose
The employee's digital identity: email, calendar, chat, meeting, git and business-app accounts, provisioned and removed automatically.

## Owns
- identities
- external accounts and their lifecycle

## API
- `ProvisionAccounts(employee)` · `RevokeAccounts(employee)`
- calendar sync → MeetingAttendance triggers

## Events
| Direction | Subjects |
|---|---|
| In | `employee.created` · `employee.paused` · employee archived |
| Out | `identity.account_provisioned` · `identity.account_revoked` |

## Depends on
- SCIM 2.0 / Google Workspace / Microsoft 365 / Slack / GitHub admin APIs
- OpenBao

## Scaling
Low traffic; 2–3 replicas. Provisioning runs as Temporal workflows.

## Failure behaviour
Provisioning is idempotent and resumable; accounts are always marked as AI (ADR-0010).

## Folder layout
```
identity-accounts/
  api/
  domain/
  adapters/scim/
  adapters/google-workspace/
  adapters/microsoft-365/
  adapters/slack/
  adapters/github/
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
- [Service catalog](../../../../architecture/services.md)
