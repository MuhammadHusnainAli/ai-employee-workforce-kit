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
| Direction | Stream | Subjects |
|---|---|---|
| In | `TASKS` (durable consumer `agent-runtime-tasks`) | `task.created` starts `EmployeeRun` (workflow id = task id, never twice) · `task.cancel_requested` · `task.approval_decided` · `task.reply_given` become signals |
| Out | `RUNS` | `run.started` · `run.step_completed` · `run.completed` · `run.failed` |

core-python runs the outbox relay and the task inbox next to the Temporal workers
(`src/core_python/agent_runtime/adapters/tasks.py`), in the same process as everything else (part
`runtime-workers`; the evaluation queues are part `runtime-eval-workers`).

## Employees
A run's employee comes from the workforce module (`WorkforceService`, on core-python's own port): `load_employee` resolves the active version
and the run is pinned to it, so publishing a new version never changes a run already going. Role and instructions
become the prompt; the model, tool grants (with risk tiers), auto-approved R2 tools and limits come from the version.
Tools are those in `tool_catalog.py`; a grant for a tool this runtime cannot run yet is skipped. Employees defined in
code (the demo `summariser`) are the fallback. Paused employees do not start runs.

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

## Folder layout (inside core-python)
A module of `core-python` ([ADR-0017](../../../../architecture/adr/0017-two-backend-services.md)), built on
`agent-harness-adk` from PyPI ([ADR-0016](../../../../architecture/adr/0016-harness-package-as-dependency.md)).

```
core-python/
  contracts/                 common types + this service's API (wfk.agent_runtime.v1)
  migrations/agent_runtime/  run_db: runs, run_steps, outbox, inbox
  src/core_python/agent_runtime/
    harness/                 the step loop: context, model, validate (decide), act, observe, loop
    workflows/               EmployeeRun (Temporal)
    activities/              one activity per harness stage
    api/ domain/ adapters/
  tests/agent_runtime/
    harness/                 stage and loop tests (FakeProvider, no network)
    replay/                  Temporal replay tests
```

## Rules (every module)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../../../architecture/services.md)
- [agent-harness.md](../../../../architecture/agent-harness.md)
