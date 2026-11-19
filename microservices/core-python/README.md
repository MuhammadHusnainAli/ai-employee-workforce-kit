# core-python

The Python backend: one self-contained service holding the modules that used to be four services
([ADR-0017](../../architecture/adr/0017-two-backend-services.md)).

| | |
|---|---|
| **Language** | Python 3.13 |
| **Port** | 7001 (gRPC, with gRPC health) |
| **Databases** | one per module: `workforce_db`, `run_db`, `brain_db`, `eval_db` |

## Modules

| Module | Package | What it does | Spec |
|---|---|---|---|
| Workforce | `core_python.workforce` | employees and their immutable versions (`WorkforceService`) | [docs/modules/workforce.md](docs/modules/workforce.md) |
| Agent runtime | `core_python.agent_runtime` | the durable step loop on Temporal (`EmployeeRun`), run ledger (`RunService`) | [docs/modules/agent_runtime.md](docs/modules/agent_runtime.md) |
| Brain | `core_python.brain` | recall, the governed write path, feedback, rules, skills (`BrainService`) | [docs/modules/brain.md](docs/modules/brain.md) |
| Evaluation | `core_python.evaluation` | golden, regression and safety suites gating brain changes | [docs/modules/evaluation.md](docs/modules/evaluation.md) |

Planned Python modules (specs only, built here when their phase comes):

| Module | What it will do | Spec |
|---|---|---|
| Model gateway | LiteLLM plugins: fallback chains, budgets, token accounting, safe caching | [docs/modules/model_gateway.md](docs/modules/model_gateway.md) |
| Meeting | joins meetings, transcribes, summarises, follows up (Phase 9) | [docs/modules/meeting.md](docs/modules/meeting.md) |
| Realtime voice | the streaming voice pipeline on Pipecat (Phase 9) | [docs/modules/realtime_voice.md](docs/modules/realtime_voice.md) |
| Tenant directory | tenants, plans, feature flags and tenant-to-cell routing (global) | [docs/modules/tenant_directory.md](docs/modules/tenant_directory.md) |
| Billing | subscriptions, invoices and credit balances from hourly usage rollups (global) | [docs/modules/billing.md](docs/modules/billing.md) |

Modules talk to each other the same way they did as services: over the gRPC contracts in `contracts/`
(on this process's own port) and over NATS events. A module never reads another module's tables.

## One process

`core-python` runs everything until SIGINT / SIGTERM:

| Part | Module | What |
|---|---|---|
| gRPC server | all | health, `WorkforceService`, `RunService`, `BrainService` on port 7001 |
| `workforce-relay` | workforce | outbox relay, `employee.*` to `EMPLOYEES` |
| `runtime-workers` | agent_runtime | Temporal queues `runs`, `llm`, `tools`; run outbox relay (`RUNS`); task inbox (`TASKS`) |
| `runtime-eval-workers` | agent_runtime | queues `eval-runs`, `eval-llm`, `eval-tools`, workflows only: evaluations never take live work |
| `brain-ingest` | brain | `BrainIngest` and nightly consolidation; `RUNS` and `EVAL` inboxes; `BRAIN` outbox |
| `evaluation` | evaluation | `EvalProposal`; `brain.change_proposed` inbox; `EVAL` outbox |

Each background part is supervised: when one fails (Temporal or NATS down), it is logged and restarted
after a pause while the gRPC server keeps serving. Readiness checks every module's database.

## Settings (env prefix `WFK_`)

| Variable | Default | |
|---|---|---|
| `WFK_PORT` | `7001` | the one port |
| `WFK_WORKFORCE_DATABASE_URL` | `.../workforce_db` | |
| `WFK_RUNTIME_DATABASE_URL` | `.../run_db` | |
| `WFK_BRAIN_DATABASE_URL` | `.../brain_db` | |
| `WFK_EVAL_DATABASE_URL` | `.../eval_db` | |
| `WFK_WORKERS` | `true` | `false`: gRPC and the workforce relay only (no Temporal needed) |
| `WFK_WORKER_MODEL` | `local-mock` | `scripted`, `local-mock`, `claude-haiku` |
| `WFK_MIGRATIONS_DIR` | `migrations` | where `core-python migrate` finds `migrations/<module>/` |

Module settings keep their names: `WFK_EXTRACTOR`, `WFK_EMBEDDER`, `WFK_VALKEY_URL`, `WFK_SUITES_DIR` and the rest
(see each module's `settings.py`).

## Commands

```
just run            # the whole service against the local stack (just up at the repo root)
just migrate        # every module's migrations, each into its own database
just ci             # lint, types, unit + integration tests, contracts, licences
just demo-run "Some text." --kill-worker-after-step 0
just bench          # brain recall latency gate
```

## Folder layout

```
core-python/
  pyproject.toml  uv.lock  justfile  Dockerfile
  contracts/proto/wfk/       common, workforce_service, agent_runtime, brain_service (wire names unchanged)
  migrations/<module>/       one folder per module database
  src/core_python/
    main.py app.py settings.py   the composed process
    workforce/ agent_runtime/ brain/ evaluation/   one package per module: api/, domain/, adapters/
  src/wfk_platform/          the platform code (one copy)
  src/wfk/                   generated from contracts/ (just gen)
  brain-core/                the brain's Rust hot path (PyO3, built by maturin)
  suites/                    evaluation suites (golden, safety)
  tests/<module>/            each module's tests; tests/platform/ and tests/test_core.py for the rest
  docs/modules/              one spec per module, built and planned
```
