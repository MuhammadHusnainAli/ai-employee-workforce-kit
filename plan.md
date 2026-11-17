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

## Phase 2: Agent Runtime walking skeleton

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

---

## Phase 3: Brain step 1 (identity core, working memory, episodic memory, Recall)

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

## Phase 5: Brain step 2 (write pipeline, governance, Inspector)

`brain-ingest` deployable, extractor through the Model Gateway, governance filter (trust tags, secret and PII drop,
instructions stored as data), resolver, one-transaction commit with outbox, batch embeddings, Brain Inspector API.

## Phase 6: Brain steps 3 and 4 (semantic graph, rules from feedback)

Entities, relations, bi-temporal facts, recursive-CTE graph recall, `Feedback` API, rules from verified corrections,
approval flow.

## Phase 7: Channels, sandbox and web app

Channel Gateway (Rust): email, Slack, Teams, Google Chat, webhooks. Sandbox Runner (Rust): gVisor pods. Web app and
public API (Next.js) with tasks, runs, approvals and the Brain Inspector.

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
