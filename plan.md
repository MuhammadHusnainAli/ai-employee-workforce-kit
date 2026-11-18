# Implementation Plan

This plan turns the architecture in [architecture/](architecture/README.md) into running code, phase by phase.
Each phase ends with something that runs and is tested. Each task ends with one git commit.

## How we work

- **One task, one commit.** The commit message is 5 to 10 words, imperative, for example
  `Add uv and cargo workspace roots`.
- **Branch:** work happens on `build/implementation`, merged to `main` at the end of each phase.
- **Done means verified.** A task is done when its "Done when" check passes locally (tests, lint, or the command
  listed), not when the code is written.
- **Detail level.** Phases 0 to 2 are planned task by task. Later phases list their tasks at a coarser grain and are
  broken down in detail when we reach them, using what the earlier phases taught us.
- **Docs style:** no em dashes in any doc, diagram or README.

---

## Decisions this plan rests on

### D1. Two languages: Rust and Python. Go is dropped.

| Language | Used for | Why |
|---|---|---|
| **Rust** | Throughput-heavy and latency-critical services, plus hot-path modules called from Python | Predictable latency, low memory per pod, strong type safety for the transactional core |
| **Python** | Temporal workflows, the agent harness, LLM-facing services, light control-plane services | Temporal Python SDK, `agent-harness-adk`, and the LLM ecosystem live here |

Temporal has no production-ready Rust SDK, so **every workflow and activity worker is Python**. Rust services sit
behind gRPC and are called from Python activities. Rust code that Python needs in-process (ranking, hashing,
token-budget packing) ships as a PyO3 module built with maturin.

| Service | Language | Reason |
|---|---|---|
| Channel Gateway | Rust | High fan-in of webhooks and email, strict dedupe |
| Task Service | Rust | Hot inbox, SKIP LOCKED queues, high write rate |
| Integration & Tool Service | Rust | 9-stage pipeline on every tool call, circuit breakers, MCP client |
| Sandbox Runner | Rust | Pod lifecycle and strict resource limits |
| Audit Service | Rust | Append-only, hash-chained, very high write volume |
| Usage Metering | Rust | Counts every token and tool call, quota checks on the hot path |
| Workspace Manager | Rust | microVM lifecycle, in-VM agent, streaming |
| Identity & Accounts | Rust | Provisioning adapters, secrets handling via OpenBao |
| Agent Runtime | Python | Temporal workflows plus `agent-harness-adk` |
| Brain Service | Python, with Rust module `brain-core` | Recall fusion, ranking and packing in Rust via PyO3 (p95 under 300 ms) |
| Model Gateway | Python | LiteLLM proxy plus plugins |
| Meeting Service | Python | Temporal workflows, LLM summarisation |
| Realtime Voice | Python | Pipecat |
| Evaluation Service | Python | Uses the `agent-harness-adk` Evaluator |
| Workforce Service | Python | Low-volume CRUD over immutable versions |
| Tenant & Cell Directory | Python | Global, low volume |
| Billing | Python | Global, low volume |
| Web App / Public API | TypeScript (Next.js) | Unchanged |

### D2. `agent-harness-adk` is a dependency; the step loop lives in Agent Runtime

[ADR-0016](architecture/adr/0016-harness-package-as-dependency.md) (supersedes ADR-0014). The package is installed
from PyPI in the `pyproject.toml` of the services that use it and is never changed from this repo. Agent Runtime
builds the durable loop from the package's public parts:

```
state = AgentState(...)                                # serialisable: messages, step, usage, status
req   = await build_context(spec, state)               # ContextAssembler, ContextCompactor  -> activity
resp  = await call_model(provider, req, state)         # Provider.complete, budget check      -> activity
dec   = decide(spec, state, resp)                      # Tool.validate, Guardrails, approvals
      # dec is exactly one of: Final | ToolCalls | AskHuman | GiveUp (or an internal Repair)
outs  = await act(spec, state, dec.calls)              # Tool.run, key run_id:step:idx       -> activity
state = observe(state, resp, dec, outs)                # pure, runs in the workflow
```

How the package's parts map to the platform:

| `agent-harness-adk` part | Platform use |
|---|---|
| `Tool`, `ToolRegistry`, `Prompt` | Employee definition at run time, built from the pinned Workforce version |
| `OpenAICompatibleProvider` | Pointed at the Model Gateway (LiteLLM). The only path to any LLM |
| `ContextAssembler`, `ContextCompactor` | Context engine stage |
| `Guardrails` | Output guardrails in the decide stage |
| `MemoryStore` contract | `BrainMemoryStore` adapter over Brain `Recall` and `Remember` (Phase 3) |
| `Evaluator`, `GoldenTask`, `Expect` | Evaluation Service suites and the release gate |
| `FakeProvider`, `RecordingProvider`, `ReplayProvider` | Deterministic tests and Temporal replay tests |

### D3. Tooling

| Concern | Choice |
|---|---|
| Python | 3.13, `uv` per service, `ruff`, `pyright`, `pytest`, `pytest-asyncio` |
| Rust | 1.99.1 pinned in each service's `rust-toolchain.toml`, one crate per service, `clippy`, `rustfmt`, `cargo-deny` (CI) |
| Rust stack | `tokio`, `tonic` + `prost` (gRPC), `axum` (health, REST), `sqlx` (Postgres), `async-nats`, `tracing` + `opentelemetry` |
| Python stack | `temporalio`, `grpcio`, `psycopg` 3 (async), `nats-py`, `opentelemetry`, `pydantic`, `fastapi` (health, REST) |
| Python and Rust bridge | PyO3 + maturin |
| Contracts | Protobuf per service with `buf` (lint in CI), CloudEvents + AsyncAPI for events. Each service owns its API |
| Migrations | Plain versioned SQL per service, expand then contract. Python: `wfk-migrate`. Rust: sqlx, built into the binary. Same file format |
| Task runner | `just` (works on Windows, macOS and Linux) |
| Local stack | Docker Compose in `deployments/`: Postgres 18 + pgvector, Temporal, NATS JetStream, Valkey, SeaweedFS (S3), Grafana LGTM |

---

## Repository layout

Every microservice is a self-contained project ([ADR-0015](architecture/adr/0015-self-contained-microservices.md)):
no root `pyproject.toml` or `Cargo.toml`, no shared libraries. A service never points outside its own folder.

```
architecture/                design docs, ADRs, diagram
deployments/                 docker compose for the local stack (docker-compose.yml)
microservices/
  templates/
    python-service/          Python template: a real, tested service named service-template
    rust-service/            Rust template: the same, in Rust
    new_service.py           copies a template to microservices/<name> and renames it
  <service>/                 one self-contained project per service:
    pyproject.toml, uv.lock     (Python)  or  Cargo.toml, Cargo.lock, rust-toolchain.toml (Rust)
    justfile, Dockerfile        commands and image, run from this folder
    contracts/                  common types (copied) + this service's own API (owned)
    migrations/                 versioned SQL
    src/<service>/              api/, domain/, adapters/
    src/wfk_platform/           this service's copy of the platform code (Rust: src/platform/)
    tests/
platform/                    Helm and Kustomize for platform components (later)
cells/<cell-id>/             per-cell Argo CD values (later)
justfile                     the local stack (up, down) and new-service only
plan.md                      this file
```

The Brain's Rust hot path (`brain-core`: fusion, ranking, packing via PyO3) lives inside
`microservices/brain-service/`, built by that service, not as a shared library.

Platform code holds **infrastructure only** (outbox, telemetry, tenancy). Domain logic never goes in it.

---

## Phase 0: Foundations (Step 0) - DONE

Goal: a repo where a new service in either language can be created, run against a local stack, and pass CI.
No business features yet.

| # | Task | Done when | Commit message |
|---|---|---|---|
| 0.1 | Add ADR-0013 "Rust and Python as the two languages", superseding the Go choice | ADR merged, listed in `adr/README.md` | `Add ADR-0013 choosing Rust and Python` |
| 0.2 | Add ADR-0014 "agent-harness-adk driven step by step by Temporal" | ADR merged and listed | `Add ADR-0014 adopting agent-harness-adk step loop` |
| 0.3 | Replace Go with Rust or Python across READMEs, `services.md`, `data.md` and each service spec, using the D1 table | `grep -rw Go` finds no language reference | `Update service languages from Go to Rust and Python` |
| 0.4 | Update the language labels in the draw.io diagram | Diagram opens, no Go labels left | `Update diagram language labels to Rust and Python` |
| 0.5 | Add root `pyproject.toml` (uv workspace), `Cargo.toml` (cargo workspace), `rust-toolchain.toml`, `.editorconfig`, `.gitignore`, `justfile` | `uv sync` and `cargo check` succeed on an empty workspace | `Add uv and cargo workspace roots` |
| 0.6 | Add `contracts/` with `buf.yaml`, `common/v1` (TenantContext, IdempotencyKey, ErrorDetail, PageRequest) and the CloudEvents envelope | `buf lint` passes | `Add protobuf contracts skeleton with buf config` |
| 0.7 | Add contract codegen: `prost`/`tonic` via `build.rs` crate `wfk-contracts`, Python stubs package `wfk-contracts` | `just gen` builds both, a test imports a generated type in each language | `Add Rust and Python codegen for contracts` |
| 0.8 | Add `deployments/docker-compose.yml` with Postgres 18 + pgvector, Temporal + UI, NATS JetStream, Valkey, SeaweedFS (S3, buckets created), Grafana LGTM (includes the OTel Collector) | `just up` starts all, health checks green | `Add local docker compose development stack` |
| 0.9 | Python `wfk-platform`: settings, structured logging, OTel setup, `/healthz` and `/readyz` | Unit tests pass | `Add Python platform lib config telemetry health` |
| 0.10 | Python `wfk-platform`: tenant-scoped db session (`SET LOCAL app.tenant_id`), outbox writer, inbox dedupe, NATS outbox relay | Integration tests against compose Postgres and NATS pass, including the rollback-publishes-nothing case | `Add Python outbox inbox and tenant session` |
| 0.11 | Rust `wfk-platform`: config, tracing and OTel, health router | Unit tests pass | `Add Rust platform crate config tracing health` |
| 0.12 | Rust `wfk-platform`: tenant-scoped transaction, outbox, inbox, NATS relay | Same integration tests as 0.10, in Rust | `Add Rust outbox inbox and tenant transaction` |
| 0.13 | Migration convention: `migrations/` per service, `wfk-migrate` runner, RLS policy template, `outbox` and `inbox` table template | `just migrate <dir> <db>` applies and reverts a sample | `Add migration convention with RLS and outbox tables` |
| 0.14 | Service templates: `templates/python-service` and `templates/rust-service` (gRPC server, health, telemetry, db, tests, Dockerfile) plus `just new-service` | A generated service of each kind builds, runs, answers `/readyz` | `Add Python and Rust service templates` |
| 0.15 | CI on GitHub Actions: ruff, pyright, pytest; fmt, clippy, cargo test; buf lint and breaking; integration tests on the compose stack; template image builds; `cargo-deny` and Python licence check for ADR-0007 | `just ci` green locally; GitHub run after first push | `Add CI for Python Rust contracts and licences` |

**Phase 0 exit:** both templates run against the local stack, CI is green, docs say Rust and Python everywhere.

### Phase 0 outcome

All 15 tasks are committed on `build/implementation`. `just ci` passes locally: lint and types clean, 57 Python
tests (42 unit, 15 integration), Rust unit and integration tests, `buf lint`, generated code up to date, licences.

Changes from the plan, and why:

| Planned | Built | Why |
|---|---|---|
| `deploy/local/compose.yaml` | `deployments/docker-compose.yml` | The folder already existed in the repo |
| `sqlx migrate` for both languages | `wfk-migrate` (Python, in `wfk-platform`) | One tool for both languages, no extra install, checksum and lock rules we control |
| Separate OTel Collector | Grafana LGTM image | It already contains the collector |
| `cargo-nextest` | `cargo test` | Fast enough at this size, one less tool |
| MinIO (as named in the request) | SeaweedFS (S3 API) | ADR-0007 already chose it for licence reasons. Any S3 client works |
| Templates as files to copy | Templates are workspace members, compiled and tested in CI | A broken template cannot be copied into a new service |

### Restructure after Phase 0

The first build used root workspaces (`pyproject.toml`, `Cargo.toml`), shared `libs/`, and root `contracts/`,
`templates/` and `tools/`. On review this was changed so every microservice is self-contained
([ADR-0015](architecture/adr/0015-self-contained-microservices.md)): own manifest and lockfile, own copy of the
platform code, own contracts, own `justfile` and Dockerfile. Templates and the generator moved to
`microservices/templates/`, and CI now checks each service from its own folder. The task table above records the
original commits; the layout section above is the current state.

Verified after the restructure: Python template 46 tests, Rust template 25 tests (including a new built-in
migration test), generator 13 tests, lint and types clean. A service generated from each template passed its own
`just ci` and built its Docker image from its own folder.

Notes for later phases:

- `psycopg` (the Python Postgres driver) is **LGPL-3.0**. The licence check allows LGPL for libraries used
  unmodified. If that is not acceptable, swap to `asyncpg` (Apache-2.0) behind `wfk_platform.db`.
- `cargo-deny` does not build on this Windows machine (its zstd dependency needs a C toolchain). CI runs it on
  Linux. The licence part was checked locally from `cargo metadata`: 326 crates, all on the allow-list.
- Rust JSON logs do not yet carry `trace_id` the way Python logs do. Add it when the first Rust service ships.
- Platform code is copied per service. A platform fix goes into the template first, then into each service.

---

## Phase 1: The step loop in Agent Runtime, on `agent-harness-adk` - DONE

`agent-harness-adk` is used as a normal PyPI dependency, unchanged ([ADR-0016](architecture/adr/0016-harness-package-as-dependency.md)).
The loop is built in `microservices/agent-runtime/src/agent_runtime/harness/`, one module per stage, each with
serialisable inputs and outputs so Phase 2 can run each one as a Temporal activity.

| # | Task | Done when | Commit message |
|---|---|---|---|
| 1.1 | ADR-0016 (supersedes ADR-0014), this plan, `CLAUDE.md` with instructions for working in this repo | Docs merged | `Add ADR-0016, Phase 1 plan and CLAUDE.md` |
| 1.2 | Scaffold `agent-runtime` from the Python template; add `agent-harness-adk>=0.2,<0.3` to its `pyproject.toml` | `just ci` in the service passes, the package imports | `Scaffold agent-runtime with agent-harness-adk dependency` |
| 1.3 | `AgentState` (messages, step, usage, loop history, status, pending calls) and `Decision` (`Final`, `ToolCalls`, `AskHuman`, `GiveUp`, plus internal `Repair`), plus `EmployeeSpec` | JSON round-trip tests pass | `Add serialisable AgentState and Decision types` |
| 1.4 | `build_context`: system prompt via `ContextAssembler`, compaction via `ContextCompactor`, open tool calls closed, tool manifest limited to granted tools | Tests: granted tools only, compaction keeps pairs intact | `Add build_context stage on ContextAssembler` |
| 1.5 | `call_model`: `Provider.complete`, budget pre-check with `estimate_cost` (stops instead of overspending) | Tests on `FakeProvider`, budget stop | `Add call_model stage with budget pre-check` |
| 1.6 | `decide`: tool-call validation with `Tool.validate`, unknown and denied tools, repair loop (max 2), output guardrails, loop detector (same call 3 times), approval gate (R3 always, R2 unless allowed, `permission="ask"`) | A test per outcome | `Add decide stage with repair and guardrails` |
| 1.7 | `act`: reads (R0) in parallel up to 5, writes serially in model order, idempotency key `run_id:step:idx` in `ToolContext.state`, results wrapped as untrusted data | Key reaches tools, ordering tests pass | `Add act stage with idempotency keys` |
| 1.8 | `observe` (pure: no I/O, no clock) and stop conditions (final, max steps, max cost, loop, give up), plus approval resume | Pure function tests pass | `Add pure observe stage and stop checks` |
| 1.9 | Step driver: `step(state)` and `run(task)`; the exit test | See exit | `Add step driver with serialised state test` |

**Phase 1 exit:** a test drives an employee one step at a time, serialising `AgentState` to JSON and back between
every step, and gets the same result as running the loop without interruption.

### Phase 1 outcome

All 9 tasks committed. `agent-harness-adk` 0.2.0 is used from PyPI, unchanged. In `microservices/agent-runtime`,
`just ci` passes: 92 unit tests (61 of them for the harness) and 15 integration tests, lint, types and licences
clean.

The exit test (`tests/harness/test_loop.py`) runs one scripted employee twice: uninterrupted, and one step at a
time with `AgentState` written to JSON and read back before every step and the employee rebuilt each time. The
model sees identical prompts, each tool runs exactly once with the same idempotency key, and the final states
match. The scenario covers a repaired invalid call, a parallel-safe read, an approval-gated R2 write and a final
answer.

Also proven by tests: a leaked secret is redacted before it reaches state; a blocked answer is withheld; the budget
check stops before any spend; a runaway loop stops at the third identical call; a declined approval is read by the
model as the call's result.

For Phase 2:

- Each stage maps to one activity: `build_context`, `call_model`, `act`. `decide` and `observe` are pure and can
  run in the workflow. `model.classify()` already sorts provider errors into retryable and non-retryable.
- `EmployeeSpec` is built in code today. Phase 2 builds it from the pinned Workforce version.

---

## Phase 2: Agent Runtime walking skeleton - DONE

A task goes in, an employee works through it with real model calls and a fake tool, and every step is durable.

| # | Task | Done when | Commit message |
|---|---|---|---|
| 2.2 | `run_db` migrations: `runs`, `run_steps`, `outbox`, `inbox`, RLS | Migrations apply and revert | `Add run_db schema with runs and run_steps` |
| 2.3 | `EmployeeRun` workflow: loop over activities `build_context`, `call_model`, `decide`, `act`; `observe` in workflow code | Workflow completes on `FakeProvider` | `Add EmployeeRun workflow driving harness steps` |
| 2.4 | Activity contract: timeouts, retry policies and error taxonomy from diagram page 2; large payloads to S3 by reference | Tests per activity for retryable and non-retryable errors | `Add activity timeouts retries and error taxonomy` |
| 2.5 | Signals: approval, human_reply, pause, resume, cancel; `WAITING_HUMAN` state | Signal tests pass | `Add approval and control signals to EmployeeRun` |
| 2.6 | Step ledger: each step writes `run_steps` plus `run.step_completed` to outbox in one transaction | Crash between steps loses nothing in a test | `Record run steps with outbox in one transaction` |
| 2.7 | Temporal replay tests in `tests/replay/` using recorded histories and `ReplayProvider` | Replay of saved histories is deterministic | `Add Temporal replay tests for EmployeeRun` |
| 2.8 | Model calls through the Model Gateway: minimal LiteLLM in compose, `OpenAICompatibleProvider` pointed at it | One real model call end to end locally | `Route harness model calls through LiteLLM gateway` |
| 2.9 | Read API for run history (gRPC `RunService.GetRun`, `ListSteps`) | Contract test passes | `Add run history read API` |
| 2.10 | Kill-the-worker test: kill the worker mid-run, a new one resumes without repeating a tool call | Test passes reliably | `Add crash resume test for agent runs` |

**Phase 2 exit:** `just demo-run "summarise this text"` runs a durable agent run, survives a worker kill, and shows
its steps.

### Phase 2 outcome

All tasks committed. In `microservices/agent-runtime`, `just ci` passes: 112 unit tests and 43 integration tests
(6 skipped: the history recorder, which runs only on purpose, and the real-model test, which needs a key), lint,
types and licences clean.

Exit: from `microservices/agent-runtime`, `just demo-run "Some text." --kill-worker-after-step 0` starts a worker
process, kills it without warning in the middle of the next model call, starts a new one, and the run completes. The
same scenario runs as a test (`tests/crash/test_demo_run.py`).

What is proven by tests:

- **Durability:** a worker process is killed while a tool is half-way through a write. A new worker finishes the
  run; the tool from the earlier step never runs again; the interrupted tool is retried with the same idempotency
  key and its effect applies once; the ledger has no duplicates (`tests/crash/test_kill_worker.py`).
- **Activity contract** (diagram page 2): timeouts, heartbeats and retry policies per activity; a 429 is retried, a
  bad key is not and fails the run cleanly; large payloads go to S3 and only a reference goes into history.
- **Signals:** approve, decline (read by the model), reply after a loop, pause and resume, cancel, approval expiry.
- **Ledger:** every transition is one `run_steps` row plus its outbox event in one transaction; a retried write adds
  nothing; a failed outbox write leaves nothing half-written.
- **Replay:** five recorded histories replay deterministically; a careless workflow change is caught.
- **Model Gateway:** LiteLLM in the local stack; a whole run goes through it with the `local-mock` model.
- **Read API:** `RunService.GetRun` and `ListSteps` over gRPC; another tenant's run is indistinguishable from no run.

Changes from the plan, and why:

| Planned | Built | Why |
|---|---|---|
| Replay tests with `ReplayProvider` | Recorded Temporal histories only | During replay Temporal takes activity results from history; the model is never called |
| One real model call end to end | Proven with LiteLLM's `local-mock`; the real-model test is written and skipped | No API key on this machine. Add `ANTHROPIC_API_KEY` to `deployments/.env` and run `just demo-run "..." --model claude-haiku` |
| | `Database(tenant_role=...)` in the platform code | The local stack logs in as a superuser, which RLS ignores. Tenant transactions now switch to the service role, so isolation holds locally exactly as in production |

For later phases:

- The scripted demo model is labelled as not an LLM in its answer; it exists so the kill demo runs without a key.
- `EmployeeSpec` still comes from a directory in code (`agent_runtime.demo`). Phase 4 reads it from Workforce.
- Unknown-outcome handling (`reconcile()`, ask a human) arrives with the Integration Service in Phase 4.

---

## Phase 3: Brain step 1 (identity core, working memory, episodic memory, Recall) - DONE

| # | Task | Commit message |
|---|---|---|
| 3.1 | Scaffold `brain-service` (deployable `brain-api` first) | `Scaffold brain-service with brain-api deployable` |
| 3.2 | `brain_db` migrations: `brain_versions` (identity core), `memories`, `episodes`, `recall_log`, pgvector HNSW, tsvector GIN, RLS | `Add brain_db schema for identity and episodes` |
| 3.3 | `brain-core` Rust crate: RRF fusion, score formula from brain.md section 3, token-budget packer; PyO3 bindings | `Add brain-core Rust ranking and packing module` |
| 3.4 | Retrievers: vector and full-text, each with 150 ms timeout, run in parallel | `Add vector and full-text recall retrievers` |
| 3.5 | `Recall` gRPC: planner, retrievers, `brain-core` fusion, packer with pinned identity and rules, `recall_log` citations | `Add Recall API with fusion and citations` |
| 3.6 | Degrade path: Valkey cache of identity core and rules, skip slow retrievers and flag the step | `Add recall degrade path with Valkey cache` |
| 3.7 | `BrainMemoryStore` adapter implementing the harness `MemoryStore` contract; agent-runtime uses it in `build_context` | `Connect agent runtime to Brain Recall` |
| 3.8 | Latency test: p95 under 300 ms on a seeded dataset | `Add Recall latency benchmark and p95 gate` |

### Phase 3 outcome

All 8 tasks committed. `just ci` passes in `microservices/brain-service` (43 unit, 35 integration tests, including
the latency gate) and in `microservices/agent-runtime` (116 unit, 45 integration).

| | |
|---|---|
| brain_db | `brain_versions` (identity core, one active version), bi-temporal `memories` with trust, importance and confidence, pgvector HNSW and full-text GIN indexes, `episodes`, `recall_log`; forced RLS on every table |
| brain-core | Rust, inside brain-service, built by maturin: RRF fusion, the brain.md score, the token-budget packer. 500 random inputs agree with an independent Python implementation of the formula |
| Recall | gRPC `Brain.Recall`: planner by step type, vector and full-text retrievers in parallel (150 ms each, skipped and flagged when slow or failing), fusion and packing in Rust, identity always pinned, every recall cited in `recall_log` |
| Degrade | Recall writes each employee's identity and rules through to Valkey. agent-runtime falls back on it when brain-api is unreachable, and carries on without memory when both are gone. Every fallback is flagged |
| agent-runtime | `BrainMemoryStore` implements agent-harness-adk's `MemoryStore` contract over Recall and gives `ContextAssembler` its prompt blocks: the pinned identity and memories wrapped as data. Each `run_steps` row cites its recall (`recall_id`, `memory_degraded`) |

Latency (`just bench` in brain-service, 10,000 memories, full gRPC path, hash embeddings):

| | p50 | p95 | p99 |
|---|---|---|---|
| One caller at a time | 20 ms | 26 ms | 28 ms |
| 8 concurrent callers (server and callers share one process) | 78 ms | 97 ms | 132 ms |

Also checked by hand, across real processes: agent-runtime's client recalled the seeded demo brain from a running
brain-service (`tools/seed_demo.py`), then fell back to the cached identity once brain-service was unreachable.

Notes for later phases:

- Embeddings are 1024-dimensional. Locally and in tests a deterministic hash embedder stands in (no model, no
  network); production sets `WFK_EMBEDDER=gateway` and an embedding model on the Model Gateway, whose latency the
  150 ms embedding timeout bounds.
- Memories are seeded straight into brain_db by tests and `tools/seed_demo.py`. The governed write path
  (Remember) is Phase 5.
- agent-runtime calls the brain with step type `planning` on the first step and `acting` after it.

---

## Phase 4: The transactional core in Rust

| # | Task | Commit message |
|---|---|---|
| 4.1 | Workforce Service (Python): employees and immutable versions (role, instructions, model policy, tool grants) | `Add workforce service with immutable employee versions` |
| 4.2 | Task Service (Rust): inbox, assignment, `task.created` starts `EmployeeRun` | `Add Rust task service with task inbox` |
| 4.3 | Integration & Tool Service (Rust): 9-stage pipeline, risk tiers R0 to R3, OpenFGA check, idempotency, circuit breaker, MCP client | `Add Rust integration service tool pipeline` |
| 4.4 | Unknown-outcome handling: provider key resend, `reconcile()`, or ask a human | `Add unknown outcome reconcile handling` |
| 4.5 | Audit Service (Rust): append-only, hash-chained, insert and select only role | `Add Rust audit service with hash chain` |
| 4.6 | Usage Metering (Rust): token and tool counters, quota checks, `usage.llm` consumer | `Add Rust usage metering with quotas` |
| 4.7 | End-to-end: task to run to tool call to audit and usage | `Add end to end task to tool test` |

---

## Phase 5: Brain step 2 (write pipeline, governance, Inspector) - DONE

`brain-ingest` deployable, extractor through the Model Gateway, governance filter (trust tags, secret and PII drop,
instructions stored as data), resolver, one-transaction commit with outbox, batch embeddings, Brain Inspector API.

| # | Task | Commit message |
|---|---|---|
| 5.1 | Governance filter in brain-core (Rust): secret drop, PII policy (keep, redact, drop), instruction detection, trust tag by source | `Add brain governance filter in Rust core` |
| 5.2 | Extractor through the Model Gateway: structured output validated against a schema, deterministic fallback, idempotent on the source event | `Add brain extractor through the model gateway` |
| 5.3 | Resolver and commit: near-duplicate merge, supersede or flag on contradiction, memories plus `brain.memory_committed` in one transaction | `Add brain resolver and transactional memory commit` |
| 5.4 | `brain-ingest` deployable: `run.completed` and `run.failed` start `BrainIngest` on queue `brain-ingest`, batch embeddings, own container | `Add brain ingest workflow fed by run events` |
| 5.5 | Brain Inspector API: list and explain memories, history, correct, delete, review flagged and quarantined | `Add Brain Inspector API` |
| 5.6 | End-to-end: a task's run is remembered and recalled by the next run, through the containers | `Add end to end remember then recall test` |

### Phase 5 outcome

All 6 tasks committed. `just ci` passes in `microservices/brain-service` (74 unit, 56 integration tests, including
the latency gate) and in `microservices/agent-runtime` (118 unit, 52 integration). `just e2e` passes through the
containers.

| | |
|---|---|
| Governance | Rust, in `brain-core/src/govern.rs`: secrets always removed (provider keys, tokens, JWTs, private keys, passwords in URLs and `key=value` pairs); PII (email, phone, card with a Luhn check, SSN, IBAN) kept, redacted or dropped by tenant policy (`tenant_policies`); text that tries to instruct the employee is flagged, stored as data, and quarantined unless a person wrote it; trust tag by origin |
| Extractor | The episode itself always (no model). With `WFK_EXTRACTOR=gateway`, facts, preferences and lessons from the Model Gateway through agent-harness-adk, as structured output validated against a schema; output that does not validate is dropped, never half-used |
| Resolver and commit | Near-duplicates reinforce the existing memory; a newer value of the same `fact_key` supersedes the old one (validity closed, history kept); a weaker contradiction is quarantined for a human. Memories, episodes and `brain.memory_committed` in one transaction, idempotent on the source event (`ingest_log`, ids derived from the event id). Embeddings in one batch per source |
| brain-ingest | `brain-service ingest`, its own container: `run.completed` / `run.failed` (now carrying task and output) start `BrainIngest` on the Temporal queue `brain-ingest`, workflow id from the event id; the outbox relays to the `BRAIN` stream. A model outage is retried by the activity; recall never waits for any of it |
| Inspector | gRPC on brain-api: `ListMemories` (with trust, source and governance findings), `GetMemory` (history chain and episode), `CorrectMemory` (human-verified, supersedes, idempotent), `DeleteMemory`, `ReviewMemory` (approving a contradiction supersedes the fact). Quarantined memories are never recalled |
| End to end | `just e2e` (`deployments/e2e/remember-then-recall.sh`): task A runs, brain-ingest stores its episode, task B of the same employee runs and its ledger cites a recall that includes A's memory, and the Inspector shows it |

Notes for later phases:

- The Brain Inspector UI is part of the web app (Phase 7); the API is ready.
- With the full container stack running on one laptop, the recall latency gate can miss under CPU contention; it
  passes with the service containers stopped. Run `just ci` in brain-service with only `just up`.
- Signals from task-service are set-style and can be repeated in a rare failure window (Phase 4.2 notes); the
  Inspector's corrections and reviews are idempotent or guarded by a precondition.

## Phase 6: Brain steps 3 and 4 (semantic graph, rules from feedback) - DONE

Entities, relations, bi-temporal facts, recursive-CTE graph recall, `Feedback` API, rules from verified corrections,
approval flow.

| # | Task | Commit message |
|---|---|---|
| 6.1 | Semantic graph schema: `entities` (aliases, embedding), bi-temporal `relations`, `memory_entities`; entity resolution by name, alias and embedding | `Add semantic graph with entity resolution` |
| 6.2 | Graph in the write path: entities and relations from the extractor (and a no-model baseline), linked to their facts; superseding a fact closes its relations | `Add entities and relations to the brain write path` |
| 6.3 | Graph recall: entities named in the query, up to 2 hops by recursive CTE, as of a time, fused with vector and full text | `Add graph recall with two hop recursive CTE` |
| 6.4 | `Feedback` API: corrections, ratings, rejected approvals; only a person's correction proposes a rule (`change_proposals`) | `Add Feedback API and rule proposals` |
| 6.5 | Approval flow: list and decide proposals, active rules pinned in Recall and the Valkey cache, revoke | `Add rule approval flow and pinned rules` |
| 6.6 | End-to-end: a correction becomes an approved rule pinned in the next run; graph recall finds a related entity | `Add end to end feedback to rule test` |

### Phase 6 outcome

All 6 tasks committed. `just ci` passes in `microservices/brain-service` (93 unit, 84 integration tests, including
the latency gate with the graph retriever added). `just e2e` runs both end-to-end checks through the containers.

| | |
|---|---|
| Graph | `entities` (normalised name, aliases, name embedding), bi-temporal `relations` that point at the fact stating them, `memory_entities`. Resolution by name, then alias, then embedding within the same type; a match learns the new spelling as an alias |
| Write path | The extractor's schema gains entities, mentions and relations; without a model, the episode's capitalised names become entities. Entity names pass governance (a name carrying a secret, or personal data the tenant redacts, is left out; quarantined memories add nothing). Entity names are embedded in the same batch as the memories. Superseding or correcting a fact closes its relations; deleting it removes them |
| Graph recall | A third retriever beside vector and full text, under the same 150 ms deadline: entities named in the query (whole words, names and aliases), then up to 2 hops by recursive CTE over relations valid at `as_of`, then the memories about what it reached, nearest first |
| Feedback | `Brain.Feedback`: a person's correction proposes a rule (`change_proposals`, risk medium or high); corrections from employees or services are only recorded; a rating credits or blames the memories its run recalled (`recall_log`); a rejected approval becomes a human-verified lesson through the governed write path. Idempotent on the caller's key |
| Rules | `ListProposals`, `DecideProposal` (people only; a high-risk rule needs a second person), `ListRules`, `RevokeRule`. Active rules, the employee's and the tenant's, are pinned in every recall and written through to the Valkey fallback; agent-runtime puts them in the prompt under "Rules you follow" |
| End to end | `deployments/e2e/feedback-to-rule.sh`: Maria's correction of a run becomes a high-risk proposal she cannot approve herself; Omar approves; the next run's recall pins the rule, and its graph retriever finds the first run's episode through the project it named |

Notes for later phases:

- Rules are versioned only by their rows (created, revoked). Folding them into `brain_versions` with shadow and
  canary comes with the safe promotion pipeline (Phase 8).
- The no-model entity extractor is coarse (capitalised names). Set `WFK_EXTRACTOR=gateway` for typed entities and
  relations from a real model.

## Phase 7: Channels, sandbox and web app

Channel Gateway (Rust): email, Slack, Teams, Google Chat, webhooks. Sandbox Runner (Rust): gVisor pods. Web app and
public API (Next.js) with tasks, runs, approvals and the Brain Inspector.

| # | Task | Commit message |
|---|---|---|
| 7.1 | Channel Gateway (Rust, 7013): bindings, signed inbound webhooks for Slack, Teams, Google Chat, email and generic webhooks, deduplicated by provider message id | `Add Rust channel gateway with signed webhooks` |
| 7.2 | Channel to task and back: inbound messages become tasks (idempotent `CreateTask`); a run's answer or approval request goes back to the thread; delivery receipts | `Turn channel messages into tasks and replies` |
| 7.3 | Sandbox Runner (Rust, 7015): `RunSandbox` with hard limits; hardened Docker locally, gVisor pod spec for Kubernetes; `usage.sandbox` | `Add Rust sandbox runner with hardened containers` |
| 7.4 | Web app (Next.js, 7020): public REST API over the task, run and brain gRPC APIs, OpenAPI, server-sent task updates | `Add web app public API with OpenAPI and SSE` |
| 7.5 | Web app pages: tasks, task detail with live run steps, approvals, Brain Inspector (memories, review, proposals, rules) | `Add web app pages for tasks and brain` |
| 7.6 | All three in docker compose, and an end-to-end check: webhook to task to reply, a sandbox run, the web API | `Add end to end channel and web app test` |

## Phase 8: Learning and evaluation (Brain steps 5 and 6)

Consolidation workflow, credit assignment from `recall_log`, Evaluation Service on the harness `Evaluator`, skills
mining, change proposals, shadow and canary, autonomy ladder.

## Phase 9: The digital employee

Identity & Accounts (Rust), Workspace Manager (Rust, Kata microVM), Meeting Service and Realtime Voice (Python,
Pipecat), live meeting harness mode.

## Phase 10: Production platform

Helm and Kustomize in `platform/`, cells and Argo CD in `cells/`, CloudNativePG clusters, Keycloak, OpenFGA,
OpenBao, KEDA, multi-AZ, DR drills, SLO dashboards.

---

## Open items to settle when we reach them

- Where the `agent-harness-adk` repository lives locally for Phase 1 (needed before task 1.1).
- Model provider keys for the first real model call in task 2.8.
- Whether Workforce Service moves to Rust later if its volume grows.
