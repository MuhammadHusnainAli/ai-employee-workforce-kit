# Task Service

| | |
|---|---|
| **Scope** | cell |
| **Language** | Go |
| **Database** | `task_db` on `pg-app` |

## Purpose
The work inbox. Creates tasks, tracks state, handles approvals and human replies, starts and signals runs.

## Owns
- tasks
- task_events (state history)
- approvals
- idempotency keys

## API
- `CreateTask` (REST + gRPC, requires Idempotency-Key)
- `GetTask` · `ListTasks` · `CancelTask`
- `DecideApproval` · `ReplyToTask`

## Events
| Direction | Subjects |
|---|---|
| In | `run.*` (state projection) · `quota.exceeded` |
| Out | `task.created` · `task.state_changed` · `approval.requested` · `approval.decided` |

## Depends on
- Temporal (start / signal `EmployeeRun`, workflow ID = task ID)
- NATS JetStream (outbox)

## Scaling
HPA on RPS, ≥ 3 replicas. p99 `CreateTask` < 300 ms.

## Failure behaviour
Task + outbox in one transaction. If Temporal is down, the start command waits in the outbox; no task is lost.

## Folder layout
```
task-service/
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
