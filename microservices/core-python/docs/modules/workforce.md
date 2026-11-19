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
- departments, titles, reporting lines and the one co-CEO (who reports to the person)
- hire_requests (an employee asks; only a person approves)
- schedules (→ Temporal Schedules)

## API
- employee CRUD · `PublishVersion` · `SetActiveVersion` (rollback)
- `GetVersion(id)` (used by runs, cached)
- `PauseEmployee` / `ResumeEmployee`
- org chart: `CreateDepartment` · `ListDepartments` · `PlaceEmployee` · `GetOrgChart` · `CanDelegate`
- hiring (a person only, actor kind HUMAN): `HireEmployee` · `DecideHireRequest`; anyone: `RequestHire` ·
  `ListHireRequests`. A hire is one transaction: employee, version 1 with the default org tools, place.

## Events
| Direction | Subjects |
|---|---|
| In | `brain.version_promoted` |
| Out | `employee.created` · `employee.version_published` · `employee.paused` · `employee.department_created` · `employee.placed` · `employee.hire_requested` · `employee.hire_decided` |

## Depends on
- Evaluation Service (gate before publishing)
- Temporal (schedules, pause batch signals)

## Scaling
Read-heavy; ≥ 3 replicas with version cache.

## Failure behaviour
Runs pin a version at start, so edits or outages never change in-flight behaviour.

## Folder layout (inside core-python)
A module of `core-python` ([ADR-0017](../../../../architecture/adr/0017-two-backend-services.md)) on port
7001 (gRPC, with gRPC health). Teams, schedules and the evaluation gate before publishing come in later phases.

```
core-python/
  contracts/                  common types + this service's API (wfk.workforce_service.v1)
  migrations/workforce/       workforce_db: employees, employee_versions (immutable, enforced by a trigger)
  src/core_python/workforce/
    api/                      WorkforceService gRPC
    domain/                   version rules: tool grants, tiers, auto-approve, limits
    adapters/                 Postgres repository; events through the outbox, relayed to the EMPLOYEES stream
  tests/workforce/
```

## Rules (every module)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../../../architecture/services.md)
