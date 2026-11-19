# AI Employee Workforce Kit: Architecture

The platform lets any organisation (a **tenant**) create **AI employees**: long-lived digital workers with a role,
instructions, tools, knowledge and permissions. You give an employee **tasks** through the UI, the API, a channel
(email, Slack, Teams, Google Chat, webhook) or a schedule, and it works through them on its own. It plans, calls
LLMs, uses tools and asks a human when policy requires it, and it leaves a complete audit trail.

An AI employee works like a real colleague:
- it **joins Zoom, Teams and Google Meet calls**, answers people **by voice** and takes notes;
- it **writes code on its own laptop**, a persistent VM;
- it has **its own email, calendar, chat and git accounts**;
- it is always **disclosed as AI**.

This folder is the reference architecture. It aims to be **hard to break**. Any single process, node,
availability zone or dependency can fail without losing a task, running an action twice, or affecting other
tenants.

| Document | What it answers |
|---|---|
| [services.md](services.md) | The two backend services, the capability modules inside them, what each owns, how they talk |
| [runtime-flow.md](runtime-flow.md) | Exact lifecycle of a task and an agent run, step by step |
| [agent-harness.md](agent-harness.md) | Inside the agent loop: context engine, model call, validation, tool router, stop conditions, evals |
| [brain.md](brain.md) | **Each employee's own brain**: memory stores, recall and write pipelines, consolidation, gated evolution, autonomy ladder, how the module is built |
| [digital-employee.md](digital-employee.md) | Meetings and live voice, own laptop, coding workflow, digital identity, disclosure |
| [data.md](data.md) | PostgreSQL topology, one database per module, tenancy, key tables |
| [reliability.md](reliability.md) | Failure-mode table, resilience patterns, SLOs |
| [security.md](security.md) | Threat model for autonomous agents and the controls for it |
| [deployment.md](deployment.md) | The two deployables, Kubernetes, cells, multi-AZ, DR, GitOps |
| [adr/](adr/) | Architecture Decision Records: why each major choice was made |
| [diagrams/ai-workforce-architecture.drawio](diagrams/ai-workforce-architecture.drawio) | draw.io diagrams (12 pages) |

Open the diagram at <https://app.diagrams.net>, in the draw.io desktop app, or in VS Code with the
*Draw.io Integration* extension. Pages:

| # | Page | Shows |
|---|---|---|
| 1 | System Architecture | Every capability module (coloured by the service that holds it), platform component and database, and how they connect |
| 2 | Agent Run Orchestration | Swimlanes across services, plus the activity timeout/retry contract and the error taxonomy |
| 3 | Agent Harness | One iteration in depth: context engine → model → validation → tool router → observe/stop |
| 4 | Tool Call Pipeline | Nine stages with failure handling, the unknown-outcome tree, the connector contract, the circuit breaker |
| 5 | Meetings & Real-time Voice | Meeting lifecycle, the streaming voice pipeline with latency budget, meeting intelligence, compliance |
| 6 | Workspace & Digital Identity | The own laptop (microVM), coding workflow, accounts, org placement, a day in the life |
| 7 | Data & PostgreSQL | CNPG HA topology, database ownership, outbox/inbox, RLS |
| 8 | Reliability | Defence-in-depth layers and 20 failure modes with their outcomes |
| 9 | Deployment & Cells | Regions, cells, namespaces (core-python, core-rust, web-app), DR, GitOps pipeline |
| 10 | Employee Brain | Identity, working memory, five long-term stores, scopes, the recall and write pipelines |
| 11 | Brain Service Internals | brain_db data model, gRPC API, ranking formula, the brain's parts in core-python, reliability |
| 12 | Brain Evolution | Consolidation, learning signals, credit assignment, safe promotion, autonomy ladder, brain safety |

---

## 1. Design principles

These rules are not negotiable. Every design choice below follows from one of them.

1. **No work lives only in memory.** Every agent run is a durable Temporal workflow persisted in PostgreSQL.
   If a pod dies mid-run, another worker continues from the last completed step.
2. **One writer per piece of data.** Each module owns its database. No module reads another module's tables, not
   even inside the same process. Data moves through APIs (synchronous) or events (asynchronous).
3. **State change and event publication commit together.** The module writes the event into an outbox table
   in the same transaction as the state change. A relay publishes it afterwards. Events are never lost and are
   never published for a rolled-back change.
4. **Every side effect is idempotent or reconciled.** Each tool call carries the key `run_id:step`. If a tool
   cannot guarantee idempotency and a call's outcome is unknown, the run stops and asks a human. It never
   retries blindly.
5. **Contain the blast radius.** Tenants are isolated by row-level security. Workloads are isolated by bulkheads
   (separate task queues and pools). Groups of tenants are isolated by **cells**. A failure stops at the
   smallest possible boundary.
6. **The model is untrusted.** LLM output is treated like user input. It never sees credentials. A policy check
   outside the model authorizes every action it requests.
7. **Boring, open-source, replaceable.** Only OSI-licensed components. Each one sits behind a standard
   interface (S3, OIDC, OpenTelemetry, SQL, MCP), so it can be replaced.
8. **Boundaries follow business capability; deployables follow need.** There are 16 cell capabilities plus 2
   global ones, cut by capability, not by entity ([ADR-0001](adr/0001-service-boundaries.md)). They are built as
   **modules of two backend services, one per language**: `core-python` and `core-rust`
   ([ADR-0017](adr/0017-two-backend-services.md)). Every deployable adds a failure mode and an operational cost,
   so a module becomes its own service only when it must scale or fail on its own. Its database, contracts and
   events are already separate, which keeps that split cheap.

## 2. Architecture at a glance

```
 Clients ──► Envoy Gateway ──► (routes by tenant → cell)
                │
   ┌────────────┴──────────────── GLOBAL CONTROL PLANE ─────────────────────────┐
   │ Keycloak (identity) · Tenant & Cell Directory · Billing · pg-global         │
   │ (Directory and Billing: planned core-python modules, deployed globally)     │
   └─────────────────────────────────────────────────────────────────────────────┘
   ┌──────────────────────────────── CELL (N copies) ───────────────────────────┐
   │ web-app   Web App + public REST API                                         │
   │ core-python (one process)                                                   │
   │           Workforce · Agent Runtime · Brain (per-employee) · Evaluation     │
   │           planned: Model Gateway plugins · Meeting · Realtime Voice         │
   │ core-rust (one binary)                                                      │
   │           Task · Channel · Sandbox                                          │
   │           planned: Integration & Tools · Audit · Usage Metering ·           │
   │           Workspace · Identity & Accounts                                   │
   │ Other workloads: sandbox pods · meeting bots · employee microVMs ·          │
   │           GPU inference pool                                                │
   │ Platform: Temporal · NATS JetStream · OpenFGA · OpenBao · Valkey ·          │
   │           SeaweedFS (S3) · Egress Proxy                                     │
   │ Data:     CloudNativePG clusters: pg-app · pg-ledger · pg-brain ·           │
   │           pg-temporal                                                       │
   └─────────────────────────────────────────────────────────────────────────────┘
 Cross-cutting: OpenTelemetry → Prometheus · Loki · Tempo · Grafana (+ Langfuse for LLM traces)
```

## 3. Technology choices

| Concern | Choice | License | Why this and not the obvious alternative |
|---|---|---|---|
| System of record | **PostgreSQL 18** | PostgreSQL | One engine for relational data, JSONB, full-text search, vectors (pgvector) and queues (SKIP LOCKED). Fewer engines means fewer failure modes |
| Postgres on Kubernetes | **CloudNativePG** | Apache-2.0 | Automated failover, synchronous quorum replication, PITR with Barman, and a PgBouncer `Pooler` resource. Kubernetes-native, CNCF |
| Vector search | **pgvector** (HNSW) | PostgreSQL | Embeddings stay in the same transaction as their source documents, so there is no second store to keep in sync |
| Durable execution | **Temporal** (on Postgres) | MIT | Retries, timers, signals (human approval), resumable state and versioning come built in. Building this yourself on queues is how agent platforms end up losing work |
| Event bus | **NATS JetStream** | Apache-2.0 | Replicated persistent streams, dedupe by message ID, explicit acks, DLQ. Much lighter to operate than Kafka at this scale. Kafka is the upgrade path if throughput ever needs it |
| API gateway | **Envoy Gateway** (Gateway API) | Apache-2.0 | JWT validation, per-tenant rate limiting, header-based routing to cells |
| Identity | **Keycloak** | Apache-2.0 | OIDC/SAML, SSO, the Organizations feature for B2B tenants, and service accounts for API clients |
| Fine-grained authz | **OpenFGA** | Apache-2.0 | Relationship-based checks ("can employee E use tool T on resource R for tenant X"). Stored in Postgres |
| Secrets | **OpenBao** | MPL-2.0 | Open-source fork of Vault (Vault is now BSL). Holds tenants' OAuth tokens and API keys |
| LLM routing | **LiteLLM Proxy** | MIT (core) | A single API in front of every provider, with fallbacks, budgets and spend logs in Postgres. Self-hosted models through **vLLM** |
| Tool protocol | **MCP** (Model Context Protocol) | open spec | Standard interface for tools. Connectors are MCP servers, so new tools need no runtime changes |
| Code sandbox | **gVisor** (runsc) / **Kata Containers** | Apache-2.0 | Kernel-level isolation for agent-generated code |
| Cache / rate limits | **Valkey** | BSD-3 | Redis-compatible and still BSD. Holds **only ephemeral data**: losing it costs latency, never correctness |
| Object storage | **SeaweedFS** (S3 API) | Apache-2.0 | Documents, run artifacts, large LLM payloads, backups. MinIO's community edition is no longer actively distributed. Any S3 store (Ceph RGW, cloud S3) can replace it |
| Mesh / network | **Cilium** + **Istio ambient** | Apache-2.0 | mTLS between all services with no sidecars, L7 policy, and network policies that deny by default |
| Autoscaling | **KEDA** | Apache-2.0 | Scales Temporal workers on task-queue backlog and consumers on stream lag, not CPU |
| Delivery | **Argo CD** (GitOps) | Apache-2.0 | Every cell is declared in Git. A rollback is a `git revert` |
| Observability | **OpenTelemetry**, Prometheus, Loki, Tempo, Grafana, **Langfuse** | Apache-2.0 / AGPL-3.0 / MIT | Traces that follow a task through every service and every LLM call |
| Real-time voice | **Pipecat**, Silero VAD, **faster-whisper**, pyannote, **Kokoro TTS** | BSD-2 / MIT / Apache-2.0 | Streaming voice-to-voice in about 1.15 s. Models are self-hosted on a GPU pool, so audio never leaves the cell |
| Meeting platforms | adapters: Zoom Meeting SDK · Teams Graph media bot · Meet Media API · browser bot fallback | vendor SDKs behind our interface | One adapter interface, so the voice pipeline is platform-agnostic |
| Own laptop | **Kata Containers** / KubeVirt microVM, KasmVNC, code-server, Playwright | Apache-2.0 / GPL-2.0 / MIT | A persistent, hardware-isolated workstation per employee. Humans can watch live and take over |

Languages ([ADR-0013](adr/0013-rust-and-python.md)), one backend service each
([ADR-0017](adr/0017-two-backend-services.md)): **`core-rust`** holds the throughput-heavy and latency-critical
modules (Task, Channel, Sandbox; later Integration, Audit, Usage, Workspace, Identity & Accounts). **`core-python`**
holds every Temporal workflow and the LLM-facing and control-plane modules: the Agent Runtime (built on
[agent-harness-adk](adr/0016-harness-package-as-dependency.md)), Workforce, Brain (with its Rust hot path
`brain-core` through PyO3) and Evaluation; later Model Gateway plugins, Meetings, Voice, Directory and Billing.
Contracts are **Protobuf (gRPC)** with `buf breaking` in CI for synchronous calls, and **CloudEvents + AsyncAPI**
for events.

## 4. Repository layout

```
/architecture               ← this folder
/microservices/core-python  the Python backend: one self-contained project (ADR-0015), one module per
                            capability, all in one process (ADR-0017)
/microservices/core-rust    the Rust backend: one crate, one module per capability, one binary
/microservices/web-app      the web app and public REST API (Next.js)
/deployments                the local stack (docker compose) and end-to-end checks
/platform                   Helm/Kustomize for Temporal, NATS, CNPG clusters, Keycloak, OpenFGA, OpenBao (later)
/cells/<cell-id>            per-cell Argo CD Application values; the cell's config is just data (later)
/connectors/<tool>          MCP servers for integrations: gmail, slack, hubspot and others (later)
```

## 5. What is deliberately *not* here

- **No Kafka, Elasticsearch, MongoDB or separate vector DB.** PostgreSQL plus NATS covers these needs at
  this stage. Add an engine only when a measured limit forces it.
- **No home-made scheduler or job queue.** Temporal Schedules and workflows cover recurring work.
- **No shared database or shared "common" library holding domain logic.** Those couple modules through the
  back door, even when the modules share a process.
- **No module reaching into another module's code.** Modules in one service still talk through their gRPC
  contracts and events, never by importing each other's domain code.
- **No synchronous call chain deeper than two hops on a user request.** Anything longer goes through a
  workflow or an event.
