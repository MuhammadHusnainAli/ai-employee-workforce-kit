# Evaluation Service

| | |
|---|---|
| **Scope** | cell |
| **Language** | Python |
| **Database** | `eval_db` on `pg-brain` |

## Purpose
Release gate for employee behaviour: golden tasks per role, per-employee regression sets, safety suites and shadow comparisons.

## Owns
- eval suites
- eval runs and reports
- shadow comparison results

## API
- `RunEval(candidate_version)` → report
- `StartShadow(candidate, n_tasks)`

## Events
| Direction | Subjects |
|---|---|
| In | `brain.change_proposed` · `employee.version_published` (candidate) |
| Out | `eval.completed` · `eval.regression_detected` |

## Depends on
- Agent Runtime (isolated eval task queue)
- Brain Service
- Langfuse

## Scaling
KEDA on eval queue; runs on an isolated task queue so evals never compete with live work.

## Failure behaviour
No report → no promotion (fail closed).

## Folder layout
```
evaluation-service/
  api/
  suites/golden/
  suites/regression/
  suites/safety/
  runners/
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
