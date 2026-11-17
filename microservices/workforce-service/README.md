# Workforce Service

| | |
|---|---|
| **Scope** | cell |
| **Language** | Python |
| **Database** | `workforce_db` on `pg-app` |

## Purpose
Defines AI employees: role, instructions, model policy, tool grants, knowledge bindings and guardrails, as immutable versions.

## Owns
- employees
- employee_versions (immutable blueprints)
- teams and reporting lines
- schedules (→ Temporal Schedules)

## API
- employee CRUD · `PublishVersion` · `SetActiveVersion` (rollback)
- `GetVersion(id)` (used by runs, cached)
- `PauseEmployee` / `ResumeEmployee`

## Events
| Direction | Subjects |
|---|---|
| In | `brain.version_promoted` |
| Out | `employee.created` · `employee.version_published` · `employee.paused` |

## Depends on
- Evaluation Service (gate before publishing)
- Temporal (schedules, pause batch signals)

## Scaling
Read-heavy; ≥ 3 replicas with version cache.

## Failure behaviour
Runs pin a version at start, so edits or outages never change in-flight behaviour.

## Folder layout
```
workforce-service/
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
