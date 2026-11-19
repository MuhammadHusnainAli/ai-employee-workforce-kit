# Task Service

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | `task_db` on `pg-app` |
| **Local port** | 7004 (HTTP health and gRPC) |

## Purpose
The work inbox. Creates tasks, tracks state, handles approvals and human replies, starts and signals runs.

## Owns
- `tasks`: one row per task. `task_id` is also the `EmployeeRun` workflow id. Status is a projection
  of the run's events; `run_seq` is the last run ledger sequence applied, so late or repeated events
  change nothing and a finished task never changes again.
- idempotency keys: `CreateTask` is unique per (tenant, key); approvals and replies derive their
  event id from (task, key), so a retry emits nothing new.

## API (gRPC, `contracts/proto/wfk/task_service/v1/tasks.proto`)
- `CreateTask` (idempotency key required) · `GetTask` · `ListTasks` · `CancelTask`
- `DecideApproval` · `ReplyToTask` (only while the task waits for a person)

## Events
| Direction | Stream | Subjects |
|---|---|---|
| Out | `TASKS` | `task.created` · `task.state_changed` · `task.cancel_requested` · `task.approval_decided` · `task.reply_given` |
| In | `RUNS` (durable consumer `task-service-runs`) | `run.started` · `run.step_completed` · `run.completed` · `run.failed` |

agent-runtime consumes `task.*`: it starts `EmployeeRun` on `task.created` and turns the other
commands into workflow signals. The task service never calls Temporal itself.

## Depends on
- NATS JetStream (outbox relay and inbox consumer run inside the process)

## Scaling
HPA on RPS, ≥ 3 replicas. p99 `CreateTask` < 300 ms.

## Failure behaviour
Task + outbox in one transaction. If NATS or agent-runtime is down, `task.created` waits in the outbox; no task is lost.

## Folder layout
```
task-service/
  src/api.rs        gRPC TaskService
  src/domain.rs     statuses and how run events move them (no I/O)
  src/adapters.rs   task_db: tasks, outbox events, run-event projection
  src/platform/     copied platform code (db, outbox, messaging, health, settings)
  migrations/       0001 outbox/inbox, 0002 tasks
  tests/            unit + integration (tasks.rs covers the API and projection)
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../../../architecture/services.md)
