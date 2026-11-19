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
- delegation: a task can be handed down from another (`parent_task_id`, `root_task_id`, `depth`). A goal is
  depth 0; sub-tasks go at most 3 levels deep and a task has at most 20. A finished sub-task emits
  `task.child_finished` to its parent (with its output, cut to 4,000 characters), which agent-runtime turns into
  the parent run's `child_finished` signal. Cancelling a task cancels every unfinished task below it.
- idempotency keys: `CreateTask` is unique per (tenant, key); approvals and replies derive their
  event id from (task, key), so a retry emits nothing new.

## API (gRPC, `contracts/proto/wfk/task_service/v1/tasks.proto`)
- `CreateTask` (idempotency key required) · `GetTask` · `ListTasks` · `CancelTask`
- `DecideApproval` · `ReplyToTask` (only while the task waits for a person)

## Events
| Direction | Stream | Subjects |
|---|---|---|
| Out | `TASKS` | `task.created` · `task.state_changed` · `task.cancel_requested` · `task.approval_decided` · `task.reply_given` · `task.child_finished` |
| In | `RUNS` (durable consumer `task-service-runs`) | `run.started` · `run.step_completed` · `run.completed` · `run.failed` |

agent-runtime consumes `task.*`: it starts `EmployeeRun` on `task.created` and turns the other
commands into workflow signals. The task service never calls Temporal itself.

## Depends on
- NATS JetStream (outbox relay and inbox consumer run inside the process)

## Scaling
HPA on RPS, ≥ 3 replicas. p99 `CreateTask` < 300 ms.

## Failure behaviour
Task + outbox in one transaction. If NATS or agent-runtime is down, `task.created` waits in the outbox; no task is lost.

## Folder layout (inside core-rust)
```
core-rust/
  src/task/api.rs       gRPC TaskService
  src/task/domain.rs    statuses and how run events move them (no I/O)
  src/task/adapters.rs  task_db: tasks, outbox events, run-event projection
  migrations/task/      0001 outbox/inbox, 0002 tasks, 0003 delegation
  tests/                unit + integration (tasks.rs covers the API and projection)
```

## Rules (every module)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../../../architecture/services.md)
