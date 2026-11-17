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

### D2. `agent-harness-adk` is the agent framework, driven step by step by Temporal

Architecture rule: every harness stage is a Temporal activity, and a crash resumes from the last completed step.
Today `Agent._drive` in `agent-harness-adk` 0.2.0 runs the whole loop inside one call. We add a **step-level public
API** to the package (released as 0.3.0) so Temporal can drive one iteration at a time:

```
state = AgentState(...)                     # serialisable: messages, plan, budget, step
ctx   = await agent.build_context(state)    # context engine        -> activity
resp  = await agent.call_model(ctx)         # model via gateway     -> activity
dec   = await agent.decide(state, resp)     # validate + repair     -> activity
      # dec is exactly one of: Final | ToolCalls | AskHuman | GiveUp
outs  = await agent.act(state, dec, key=f"{run_id}:{step}")   # tool router -> activity
state = agent.observe(state, dec, outs)     # pure, runs in the workflow
```

`Agent.run()` is rebuilt on top of these same functions, so local use is unchanged. Approvals, pause, resume and
cancel become Temporal signals in agent-runtime. The harness's own `Approvals` store is not used there.

How the harness parts map to the platform:

| `agent-harness-adk` part | Platform use |
|---|---|
| `Agent`, `Tool`, `Skill`, `Prompt`, `PromptLibrary` | Employee definition at run time, built from the pinned Workforce version |
| `OpenAICompatibleProvider` | Pointed at the Model Gateway (LiteLLM). The only path to any LLM |
| `ContextAssembler`, `ContextCompactor` | Context engine stage, with the token budget split from agent-harness.md |
| `Guardrails`, `NoSecrets`, `NoPII`, `InjectionDetector` | Output guardrails in the validate stage |
| `PolicyGate`, `HookEngine` | Local pre-check before the Integration Service authorizes (defence in depth) |
| `MemoryStore` contract | `BrainMemoryStore` adapter that calls Brain `Recall` and emits `Remember` events |
| `Evaluator`, `GoldenTask`, `Expect` | Evaluation Service suites and the release gate |
| `RecordingProvider`, `ReplayProvider`, `FakeProvider` | Deterministic tests and Temporal replay tests |
| `Tracer` | Bridged to OpenTelemetry |

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

## Phase 1: `agent-harness-adk` 0.3.0, the step-level API

Work happens in the `agent-harness-adk` repository, then this repo pins the new version.

| # | Task | Done when | Commit message |
|---|---|---|---|
| 1.1 | Define `AgentState` (pydantic, JSON-serialisable: messages, plan, todos, budget used, step, version) and `Decision` (`Final`, `ToolCalls`, `AskHuman`, `GiveUp`) | Round-trip serialisation tests pass | `Add serialisable AgentState and Decision types` |
| 1.2 | Extract `build_context(state)` from `_drive` (assembler, compaction, skills, memory recall) | Same prompts as before on the existing test suite | `Extract build_context stage from agent loop` |
| 1.3 | Extract `call_model(context)` with provider, router fallbacks and budget pre-check | Existing provider tests pass | `Extract call_model stage from agent loop` |
| 1.4 | Extract `decide(state, response)`: tool-call parsing, schema validation, repair loop (max 2), guardrails, loop detector | Tests for each outcome type pass | `Extract decide stage with repair and guardrails` |
| 1.5 | Extract `act(state, decision, idempotency_key)`: parallel reads, serial writes, `ToolContext.idempotency_key` | Key reaches tools, ordering tests pass | `Extract act stage with idempotency keys` |
| 1.6 | Extract pure `observe(state, decision, outcomes)` and stop conditions | Pure function tests pass, no I/O | `Extract pure observe stage and stop checks` |
| 1.7 | Rebuild `Agent.run()` and `stream()` on the five stages | All 296+ existing tests pass unchanged | `Rebuild agent run loop on step stages` |
| 1.8 | Docs, example `examples/30_step_api.py`, changelog, release 0.3.0 to PyPI | Package installs from PyPI | `Release step-level API as version 0.3.0` |
| 1.9 | In this repo, pin `agent-harness-adk>=0.3,<0.4` in the workspace | `uv sync` resolves it | `Pin agent-harness-adk 0.3 in workspace` |

**Phase 1 exit:** a test drives an agent one step at a time from a serialised state, with identical results to `run()`.

---

## Phase 2: Agent Runtime walking skeleton

A task goes in, an employee works through it with real model calls and a fake tool, and every step is durable.

| # | Task | Done when | Commit message |
|---|---|---|---|
| 2.1 | Scaffold `microservices/agent-runtime` from the Python template | Service runs, `/readyz` green | `Scaffold agent-runtime from Python template` |
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
