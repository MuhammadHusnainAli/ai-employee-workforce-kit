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
A self-contained Python project ([ADR-0015](../../architecture/adr/0015-self-contained-microservices.md)), built on
`agent-harness-adk` from PyPI ([ADR-0016](../../architecture/adr/0016-harness-package-as-dependency.md)).

```
agent-runtime/
  pyproject.toml  uv.lock  justfile  Dockerfile
  contracts/                 common types + this service's API (wfk.agent_runtime.v1)
  migrations/                run_db: runs, run_steps, outbox, inbox
  src/agent_runtime/
    harness/                 the step loop: context, model, validate (decide), act, observe, loop
    workflows/               EmployeeRun (Temporal)
    activities/              one activity per harness stage
    api/ domain/ adapters/
  src/wfk_platform/          this service's copy of the platform code
  tests/
    harness/                 stage and loop tests (FakeProvider, no network)
    replay/                  Temporal replay tests
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
