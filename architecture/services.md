# Services and Modules

The backend is **two services, one per language** ([ADR-0017](adr/0017-two-backend-services.md)), plus the web app.
Every capability below is a **module** inside one of them. A module keeps everything a service had except its own
process: its own database, its own API contract, its own events, its own tests and spec
(`microservices/core-*/docs/modules/`). It becomes its own service only when it must scale or fail on its own.

| Deployable | Language | Port (local) | Process | Modules |
|---|---|---|---|---|
| `core-python` | Python (+ Rust `brain-core` via PyO3) | 7001, gRPC with gRPC health | one process: the gRPC server, every Temporal worker, every outbox relay and inbox, each supervised and restarted on failure | Workforce, Agent Runtime, Brain, Evaluation, Collaboration. Planned: Model Gateway plugins, Meeting, Realtime Voice, Tenant & Cell Directory, Billing |
| `core-rust` | Rust | 7002, HTTP (health, webhooks) and gRPC on one listener | one binary, every module's routes, services and loops | Task, Channel, Sandbox. Planned: Integration & Tools, Audit, Usage Metering, Workspace, Identity & Accounts |
| `web-app` | TypeScript (Next.js) | 7000 | the web app and the public REST API | |

Global modules (Directory, Billing) run as a separate global deployment of their service, once, not per cell.
**Cell** modules run in every cell (see [deployment.md](deployment.md)). Each module owns exactly one database. A
module never connects to another module's database, even inside the same process: each module has its own pool and
its own database, and in production its own role and `pg_hba` rules.

Communication rules:
- **Commands and queries that need an answer now** use gRPC (internal) or REST/JSON (public, via Envoy). Every call
  has a deadline, and the deadline propagates downstream.
- **Facts that something happened** are CloudEvents on NATS JetStream, published through the transactional outbox.
- **Long-running work** runs as a Temporal workflow. Modules never coordinate a multi-step process through chains
  of events (no "choreography spaghetti").
- **Inside one service** the rules do not change: a module calls another module through its gRPC contract on the
  service's own port, and reacts to its events through the inbox. It never imports another module's domain code.

---

## Global control plane

### Tenant & Cell Directory (core-python, planned)
- **Owns:** tenants, organisations, plans, tenant → cell mapping, feature flags per tenant.
- **API:** `ResolveCell(tenant_id)` (served from an in-memory cache and pushed to Envoy), tenant CRUD, plan changes.
- **DB:** `directory_db` on `pg-global`.
- **Events out:** `tenant.created`, `tenant.plan_changed`, `tenant.suspended`, `tenant.moved_cell`.
- **Failure behaviour:** Envoy keeps the last-known routing table. If the Directory is down, existing tenants keep
  working; only onboarding stops.

### Identity (Keycloak)
- One realm per environment. Each tenant is a Keycloak **Organization**. Users, SSO (SAML/OIDC federation), MFA,
  and service accounts for API keys.
- Tokens carry `tenant_id`, `org_roles` and `cell`. Envoy validates JWTs locally against cached JWKS, so Keycloak
  is **not** in the request path.
- **DB:** `keycloak_db` on `pg-global`.

### Billing (core-python, planned)
- **Owns:** subscriptions, invoices, credit balances.
- **Input:** hourly usage rollups from each cell's Usage Metering module (`usage.rollup` events).
- **DB:** `billing_db` on `pg-global`. It can be swapped for Lago (AGPL) if a full billing engine is wanted.

---

## Cell modules

### 1. Channel (core-rust, module `channel`)
- **Purpose:** inbound and outbound conversation channels: email (IMAP/SMTP or provider webhooks), Slack, Teams,
  Google Chat, generic webhooks. Turns an external message into a task, and a task reply into an external message.
- **Owns:** channel bindings (which inbox or Slack channel maps to which employee), inbound message log, delivery
  receipts.
- **Key behaviour:** verifies webhook signatures, **dedupes** using the provider's message ID (unique index), then
  calls `TaskService.CreateTask` with `idempotency_key = provider:message_id`. Answers go back from `run.*` events
  (consumer `channel-gateway-runs`), sent with retries and signed per provider. Providers post to
  `/hooks/{kind}/{binding_id}` on core-rust's port.
- **DB:** `channel_db` (pg-app). **Events out:** `channel.binding_created`, `channel.message_received`,
  `channel.message_delivered`, `channel.delivery_failed`.

### 2. Task (core-rust, module `task`)
- **Purpose:** the work inbox. Creates tasks, tracks their state, handles human approvals and human replies, and
  starts and signals runs.
- **Owns:** `tasks`, `task_events` (state history), `approvals`, `idempotency_keys`.
- **Key behaviour:** `CreateTask` writes the task and a `task.created` outbox row in one transaction and returns
  at once. The Agent Runtime consumes it (consumer `agent-runtime-tasks`) and starts Temporal workflow
  `EmployeeRun` with **workflow ID = task ID** and duplicate starts rejected, so a task never runs twice. Approval
  decisions, replies and cancellations become `task.*` events that the Agent Runtime turns into Temporal
  **signals**. Run progress comes back as `run.*` events (consumer `task-service-runs`).
- **DB:** `task_db` (pg-app). **Events out:** `task.created`, `task.state_changed`, `task.approval_decided`,
  `task.reply_given`, `task.cancel_requested`, `task.completed`.

### 3. Workforce (core-python, module `workforce`)
- **Purpose:** defines AI employees.
- **Owns:** `employees`, `employee_versions` (an **immutable** blueprint: role, instructions, model policy, tool
  grants, knowledge bindings, guardrails), the org chart (departments, titles, reporting lines, exactly one
  co-CEO who reports to the person), hire requests, schedules (written through to Temporal Schedules).
- **Hiring:** a hire costs money, so only a person hires (`HireEmployee`) or approves a hire an employee asked
  for (`RequestHire`, `DecideHireRequest`). A new hire starts with the org tools; `CanDelegate` answers who may
  hand work to whom (the co-CEO to anyone, everyone else to the people below them).
- **Key behaviour:** editing an employee creates a new version. A run pins the version it started with, so an
  edit never changes a run that is already in flight. Rollback means pointing `active_version` back to an older
  version.
- **DB:** `workforce_db` (pg-app). **Events out:** `employee.created`, `employee.version_published`,
  `employee.version_activated`, `employee.paused`, `employee.resumed`, `employee.department_created`,
  `employee.placed`, `employee.hire_requested`, `employee.hire_decided`.

### 3b. Collaboration (core-python, module `collaboration`)
- **Purpose:** conversations between employees and with the person: direct messages, calls (a question and
  answer exchange, at most 6 questions) and standups run by the co-CEO. The person can read every conversation
  and join any of them.
- **Standups:** the `Standup` Temporal workflow (queue `collab`) reads each employee's real task status, writes
  one short update per active employee (a fixed line, no model call, for anyone idle), opens a discussion the
  person can join, and ends with the co-CEO's summary: where we are, where we go, next steps with owners, risks.
- **DB:** `collab_db` (pg-app). **Events out:** `collab.message_posted`, `collab.standup_started`,
  `collab.standup_end_requested`, `collab.standup_finished`. Spec: `core-python/docs/modules/collaboration.md`.

### 4. Agent Runtime (core-python, module `agent_runtime`)
- **Purpose:** executes the agent loop: recall → think (LLM) → authorize → act (tool) → observe → repeat.
- **Stateless pods.** All run state lives in Temporal history. Large payloads (prompts, tool outputs) go to
  SeaweedFS, and only a reference passes through history (claim-check pattern).
- **Task queues (bulkheads):** `runs` (workflow logic), `llm` (model calls), `tools` (tool calls), each with its
  own worker and concurrency limit, so slow LLMs cannot starve tool execution. Evaluation runs use their own
  queues (`eval-runs`, `eval-llm`, `eval-tools`) and never take live work. Today all of them run inside
  core-python; a queue's workers become their own deployment when KEDA scaling on its backlog is needed.
- **Owns:** the run ledger (`runs`, `run_steps`): a queryable, human-readable record of every step, kept for the UI
  and for audits.
- **DB:** `run_db` (pg-ledger). **Events out:** `run.started`, `run.step_completed`, `run.completed`, `run.failed`.

### 5. Model Gateway (LiteLLM Proxy, plugins planned in core-python)
- **Purpose:** the only path to any LLM. Provider abstraction, fallback chains (e.g. Claude → secondary provider →
  self-hosted vLLM), per-tenant and per-employee budgets, token accounting, response caching where safe.
- **Key behaviour:** rejects calls that exceed a budget before they reach the provider. Emits a `usage.llm` event
  with input and output tokens and cost for every call. Retries with jitter only on retryable status codes
  (429/5xx).
- **DB:** `gateway_db` (pg-app). **Cache/rate limit:** Valkey.

### 6. Integration & Tools (core-rust, planned)
- **Purpose:** everything an employee can *do* in the outside world.
- **Owns:** connector registry (MCP servers and their tool schemas), tenant connections (OAuth grants), tool risk
  tiers, the tool-call ledger (intent → result).
- **Key behaviour, for every tool call:**
  1. **Authorize:** OpenFGA check (`employee E can_use tool T` and `tool T can_access resource R`), plus a policy
     check on the risk tier.
  2. **Record intent** in `tool_calls` with `idempotency_key = run_id:step` (unique). If the key already shows
     success, return the stored result.
  3. **Inject the credential** from OpenBao at this boundary. The model and the runtime never see it.
  4. **Execute** through the MCP server. All outbound traffic goes through the egress proxy, which applies a
     per-connector domain allowlist.
  5. **Record the result** and an outbox event in the same transaction.
- **DB:** `integration_db` (pg-app). **Secrets:** OpenBao. **Authz:** OpenFGA.

### 7. Sandbox (core-rust, module `sandbox`)
- **Purpose:** runs agent-generated code (Python/JS, data analysis, file transforms).
- **Isolation:** each run is a short-lived pod on a dedicated node pool using the gVisor `runsc` RuntimeClass, with
  CPU, memory and time limits, a read-only root filesystem, no service-account token, and egress limited to the
  egress proxy (default: none). The module launches and supervises these pods; the code never runs inside
  core-rust. Locally, hardened Docker containers stand in for the pods.
- **Owns:** one row per execution (`executions`, for idempotency). **DB:** `sandbox_db` (pg-app).
  **Events out:** `usage.sandbox`.

### 8. Brain (core-python, module `brain`)
- **Purpose:** each employee's own brain: identity core, episodic memory, semantic graph, skills, preferences and
  rules, reflections. Tenant documents are its tenant-scope knowledge. Full design: [brain.md](brain.md).
- **Key behaviour:** `Recall` is synchronous (p95 under 300 ms, degrades to the cached identity core and rules).
  `Remember` is asynchronous and idempotent, and passes a governance filter that tags trust and blocks poisoning.
  Nightly consolidation and gated evolution (proposal → eval → shadow → canary) apply changes. Facts are
  bi-temporal and never overwritten.
- **Parts:** the API (Recall, Inspector, feedback, rules), ingest, nightly consolidation and learning. Today they
  run inside core-python; the target split into `brain-api`, `brain-ingest`, `brain-consolidate` and
  `brain-learn` deployables ([brain.md](brain.md) section 8) applies when the brain must scale on its own.
- **DB:** `brain_db` (**pg-brain**, its own cluster). **Raw files:** SeaweedFS.
  **Events out:** `brain.memory_committed`, `brain.memory_corrected`, `brain.memory_deleted`,
  `brain.memory_reviewed`, `brain.feedback_given`, `brain.change_proposed`, `brain.rule_activated`,
  `brain.skill_activated`, `brain.change_rejected`, `brain.rule_revoked`.

### 9. Audit (core-rust, planned)
- **Purpose:** an immutable record of everything humans and employees did. It answers "why did the AI do that?".
- **Input:** consumes `*.>` domain events from JetStream (inbox-deduped).
- **Key behaviour:** append-only table. The database role has `INSERT` and `SELECT` only. Events are hash-chained
  per tenant, so tampering is detectable. A nightly export goes to object storage with object lock.
- **DB:** `audit_db` (pg-ledger).

### 10. Usage Metering (core-rust, planned)
- **Purpose:** counts LLM tokens, tool calls, sandbox seconds and storage per tenant and per employee, and enforces
  plan quotas in near real time.
- **Key behaviour:** consumes `usage.*` events. Writes raw events to a monthly-partitioned table and aggregates
  them hourly. Publishes `usage.rollup` to Billing and `quota.exceeded`, which Task and Model Gateway act on.
- **DB:** `usage_db` (pg-ledger).

### 11. Meeting (core-python, planned)
- **Purpose:** joins Zoom, Teams and Google Meet as the employee, keeps the transcript, produces notes and action
  items.
- **Owns:** meeting attendances, bot sessions, transcripts, notes, action items.
- **Key behaviour:** one `MeetingAttendance` Temporal workflow per employee per meeting (ID =
  `platform:meeting_id:employee_id`) supervises a bot pod from a warm pool. It uses heartbeats, relaunches the bot
  on a crash, and checkpoints the transcript every 5 s. Details: [digital-employee.md](digital-employee.md).
- **DB:** `meeting_db` (pg-ledger). **Events out:** `meeting.*`.

### 12. Realtime Voice (core-python, planned)
- **Purpose:** live listening and speaking in meetings. A Pipecat pipeline runs VAD → STT → diarization →
  addressing/turn-taking → response engine → speak policy → TTS.
- **Stateless workers** next to the bot pods, calling the GPU inference pool. The low-latency pipeline is the
  likeliest first module to run as its own deployment. Degrades to a CPU model, then to
  chat-only answers.

### 13. Workspace (core-rust, planned)
- **Purpose:** each employee's own laptop: a persistent Kata microVM with a desktop, IDE, toolchains and a home
  volume.
- **Owns:** workspaces, volumes, snapshots, live-view and takeover sessions.
- **Key behaviour:** starts when a task needs it, hibernates after 30 minutes idle, resumes in under 30 s, and
  rebuilds from the golden image when unhealthy. The Agent Runtime drives the in-VM workspace agent over mTLS.
- **DB:** `workspace_db` (pg-app).

### 14. Identity & Accounts (core-rust, planned)
- **Purpose:** the employee's digital identity: email, calendar, chat, meeting, git and business-app accounts.
- **Key behaviour:** provisions and de-provisions through SCIM 2.0 and admin APIs. Credentials (passwords, TOTP
  seeds, keys) live only in OpenBao. Every account is marked as AI ([ADR-0010](adr/0010-ai-employees-are-disclosed.md)).
- **DB:** `identity_db` (pg-app). **Events out:** `identity.account_provisioned`, `identity.account_revoked`.

### 15. Evaluation (core-python, module `evaluation`)
- **Purpose:** the release gate for employee behaviour. Runs per-role golden tasks, each employee's regression set
  and the safety suite (prompt injection, policy) against candidate brain or employee versions. Also runs
  shadow comparisons on live tasks.
- **Key behaviour:** every `brain.change_proposed` starts one evaluation (consumer `evaluation-proposals`); the
  verdict goes out as `eval.completed` (and `eval.regression_detected`), and the Brain records it. No promotion if
  any metric regresses or the report is missing.
- **DB:** `eval_db` (pg-brain).

### 16. Web App / Public API (`web-app`)
- **Web App:** Next.js frontend. Calls the public REST API through Envoy, the same API customers use, so there is
  no hidden backdoor API.
- **Public API:** REST/JSON with an OpenAPI spec, generated from the same protobuf contracts (gRPC-gateway).
  Provides webhooks for task completion. Mutating calls require an `Idempotency-Key` header.

---

## Platform components (per cell)

| Component | Role | HA setup |
|---|---|---|
| Temporal | Durable workflows for runs, ingestion and schedules | frontend/history/matching/worker services ×3, history shards fixed at creation (e.g. 512), on `pg-temporal` |
| NATS JetStream | Domain event streams | 3-node cluster, streams with `replicas: 3`, file storage |
| OpenFGA | Authorization checks | 3 replicas, stateless, on `openfga_db` (pg-app) |
| OpenBao | Tenant credentials, encryption keys | 3-node Raft cluster, auto-unseal via KMS/HSM |
| Valkey | Rate limits, short caches | Primary + replica with Sentinel. **Ephemeral only** |
| SeaweedFS | S3 object storage | Master ×3, volume servers with replication `010` (across racks/AZs) |
| Egress proxy | Outbound allowlist for tools and sandbox | Envoy or Squid ×3, per-connector domain policy |
| GPU inference pool | STT (faster-whisper), TTS (Kokoro), diarization (pyannote), vLLM | GPU node pool, autoscaled on queue depth, CPU fallback models |

## Event catalogue (NATS subjects)

All events use the CloudEvents envelope with `id` (UUIDv7), `source`, `type`, `tenantid` and `traceparent`. The
JetStream `Nats-Msg-Id` header equals the CloudEvents `id`, so duplicate publishes are removed inside the dedupe
window.

Built today (stream, subjects, producer, consumers with their durable consumer names):

| Stream | Subjects | Producer | Consumers |
|---|---|---|---|
| `COLLAB` | `collab.>` | Collaboration | Collaboration (`collaboration-standups`: starts and signals standups) |
| `EMPLOYEES` | `employee.>` | Workforce | (Agent Runtime reads versions through `WorkforceService`); Audit later |
| `TASKS` | `task.>` | Task | Agent Runtime (`agent-runtime-tasks`: starts and signals runs), Web App (live updates over SSE) |
| `RUNS` | `run.>` | Agent Runtime | Task (`task-service-runs`: state), Brain (`brain-ingest-runs`: remember), Channel (`channel-gateway-runs`: answers) |
| `BRAIN` | `brain.>` | Brain | Evaluation (`evaluation-proposals`: `brain.change_proposed`) |
| `EVAL` | `eval.>` | Evaluation | Brain (`brain-evals`: `eval.completed`) |
| `CHANNELS` | `channel.>` | Channel | Audit later |
| `SANDBOX` | `usage.sandbox` | Sandbox | Usage Metering later |

Planned with their modules:

| Stream | Subjects | Producers | Main consumers |
|---|---|---|---|
| `TENANT` | `tenant.>` | Directory | every cell module (cache invalidation) |
| `TOOL` | `tool.>` | Integration | Audit, Usage |
| `USAGE` | `usage.>`, `quota.>` | Model Gateway, Integration, Usage | Usage, Task, Model Gateway |
| `MEETING` | `meeting.>` | Meeting | Brain (meeting memory), Task (action items), Audit |
| `DLQ` | `dlq.>` | (max-deliver advisories) | on-call alert + replay tool |
