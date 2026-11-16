# Agent Runtime (harness)

| | |
|---|---|
| **Scope** | cell |
| **Language** | Python |
| **Database** | `run_db` on `pg-ledger` |

## Purpose
Temporal workers that execute the `EmployeeRun` workflow and the agent harness: context → model → validate → act → observe.

## Owns
- run ledger (`runs`, `run_steps`)

## API
- Temporal workflows: `EmployeeRun`; signals: approval, human_reply, pause, resume, cancel
- read API for run history (UI)

## Events
| Direction | Subjects |
|---|---|
| In | (driven by Temporal task queues) |
| Out | `run.started` · `run.step_completed` · `run.completed` · `run.failed` |

## Depends on
- Temporal
- Workforce
- Brain Service (`Recall`)
- Model Gateway
- Integration
- Sandbox
- Workspace Manager

## Scaling
KEDA per task queue (`runs`, `llm`, `tools`, `sandbox`) on backlog.

## Failure behaviour
Stateless workers; all state in Temporal history. A crash resumes from the last completed activity.

## Folder layout
```
agent-runtime/
  workflows/
  activities/
  harness/context/
  harness/model/
  harness/validate/
  harness/act/
  harness/observe/
  migrations/
  tests/replay/
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../architecture/services.md)
- [agent-harness.md](../../architecture/agent-harness.md)
