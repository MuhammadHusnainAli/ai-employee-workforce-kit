# Service Catalog

Services are grouped by where they run. **Global** services exist once. **Cell** services are deployed once per cell
(see [deployment.md](deployment.md)). Each service owns exactly one database. A service never connects to another
service's database: database roles and `pg_hba` rules enforce this, not just convention.

Communication rules:
- **Commands and queries that need an answer now** use gRPC (internal) or REST/JSON (public, via Envoy). Every call
  has a deadline, and the deadline propagates downstream.
- **Facts that something happened** are CloudEvents on NATS JetStream, published through the transactional outbox.
- **Long-running work** runs as a Temporal workflow. Services never coordinate a multi-step process through chains
  of events (no "choreography spaghetti").

---

## Global control plane

### Tenant & Cell Directory
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

### Billing
- **Owns:** subscriptions, invoices, credit balances.
- **Input:** hourly usage rollups from each cell's Usage Metering service (`usage.rollup` events).
- **DB:** `billing_db` on `pg-global`. It can be swapped for Lago (AGPL) if a full billing engine is wanted.

---

## Cell services

### 1. Channel Gateway
- **Purpose:** inbound and outbound conversation channels: email (IMAP/SMTP or provider webhooks), Slack, Teams,
  Google Chat, generic webhooks. Turns an external message into a task, and a task reply into an external message.
- **Owns:** channel bindings (which inbox or Slack channel maps to which employee), inbound message log, delivery
  receipts.
- **Key behaviour:** verifies webhook signatures, **dedupes** using the provider's message ID (unique index), then
  calls `Task.CreateTask` with `idempotency_key = provider:message_id`. Outbound sends are activities driven by the
  run workflow.
- **DB:** `channel_db` (pg-app). **Events out:** `channel.message_received`, `channel.message_delivered`.

### 2. Task Service
- **Purpose:** the work inbox. Creates tasks, tracks their state, handles human approvals and human replies, and
  starts and signals runs.
- **Owns:** `tasks`, `task_events` (state history), `approvals`, `idempotency_keys`.
- **Key behaviour:** `CreateTask` writes the task and an outbox row in one transaction and returns `202`. The
  outbox relay starts Temporal workflow `EmployeeRun` with **workflow ID = task ID**, so a duplicate start is a
  no-op. An approval decision becomes a Temporal **signal**.
- **DB:** `task_db` (pg-app). **Events out:** `task.created`, `task.state_changed`, `approval.requested`,
  `approval.decided`.

### 3. Workforce Service
- **Purpose:** defines AI employees.
- **Owns:** `employees`, `employee_versions` (an **immutable** blueprint: role, instructions, model policy, tool
  grants, knowledge bindings, guardrails), teams and reporting lines, schedules (written through to Temporal
  Schedules).
- **Key behaviour:** editing an employee creates a new version. A run pins the version it started with, so an
  edit never changes a run that is already in flight. Rollback means pointing `active_version` back to an older
  version.
- **DB:** `workforce_db` (pg-app). **Events out:** `employee.created`, `employee.version_published`,
  `employee.paused`.

### 4. Agent Runtime (Temporal workers)
- **Purpose:** executes the agent loop: recall → think (LLM) → authorize → act (tool) → observe → repeat.
- **Stateless pods.** All run state lives in Temporal history. Large payloads (prompts, tool outputs) go to
  SeaweedFS, and only a reference passes through history (claim-check pattern).
- **Task queues (bulkheads):** `runs` (workflow logic), `llm` (model calls), `tools` (tool calls),
  `sandbox` (code execution). Each queue has its own worker pool and concurrency limit, so slow LLMs cannot
  starve tool execution.
- **Owns:** the run ledger (`runs`, `run_steps`): a queryable, human-readable record of every step, kept for the UI
  and for audits.
- **DB:** `run_db` (pg-ledger). **Events out:** `run.started`, `run.step_completed`, `run.completed`, `run.failed`.

### 5. Model Gateway (LiteLLM Proxy)
- **Purpose:** the only path to any LLM. Provider abstraction, fallback chains (e.g. Claude → secondary provider →
  self-hosted vLLM), per-tenant and per-employee budgets, token accounting, response caching where safe.
- **Key behaviour:** rejects calls that exceed a budget before they reach the provider. Emits a `usage.llm` event
  with input and output tokens and cost for every call. Retries with jitter only on retryable status codes
  (429/5xx).
- **DB:** `gateway_db` (pg-app). **Cache/rate limit:** Valkey.

### 6. Integration & Tool Service
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

### 7. Sandbox Runner
- **Purpose:** runs agent-generated code (Python/JS, data analysis, file transforms).
- **Isolation:** short-lived pods on a dedicated node pool using the gVisor `runsc` RuntimeClass, with CPU, memory
  and time limits, a read-only root filesystem, no service-account token, and egress limited to the egress proxy
  (default: none).
- **Stateless.** Inputs and outputs are SeaweedFS object references.

### 8. Brain Service
- **Purpose:** each employee's own brain: identity core, episodic memory, semantic graph, skills, preferences and
  rules, reflections. Tenant documents are its tenant-scope knowledge. Full design: [brain.md](brain.md).
- **Key behaviour:** `Recall` is synchronous (p95 under 300 ms, degrades to the cached identity core and rules).
  `Remember` is asynchronous and idempotent, and passes a governance filter that tags trust and blocks poisoning.
  Nightly consolidation and gated evolution (proposal → eval → shadow → canary) apply changes. Facts are
  bi-temporal and never overwritten.
- **Deployables:** `brain-api`, `brain-ingest`, `brain-consolidate`, `brain-learn` (one codebase, separate failure
  domains).
- **DB:** `brain_db` (**pg-brain**, its own cluster). **Raw files:** SeaweedFS.
  **Events out:** `brain.memory_committed`, `brain.version_promoted`.

### 9. Audit Service
- **Purpose:** an immutable record of everything humans and employees did. It answers "why did the AI do that?".
- **Input:** consumes `*.>` domain events from JetStream (inbox-deduped).
- **Key behaviour:** append-only table. The database role has `INSERT` and `SELECT` only. Events are hash-chained
  per tenant, so tampering is detectable. A nightly export goes to object storage with object lock.
- **DB:** `audit_db` (pg-ledger).

### 10. Usage Metering
- **Purpose:** counts LLM tokens, tool calls, sandbox seconds and storage per tenant and per employee, and enforces
  plan quotas in near real time.
- **Key behaviour:** consumes `usage.*` events. Writes raw events to a monthly-partitioned table and aggregates
  them hourly. Publishes `usage.rollup` to Billing and `quota.exceeded`, which Task and Model Gateway act on.
- **DB:** `usage_db` (pg-ledger).

### 11. Meeting Service
- **Purpose:** joins Zoom, Teams and Google Meet as the employee, keeps the transcript, produces notes and action
  items.
- **Owns:** meeting attendances, bot sessions, transcripts, notes, action items.
- **Key behaviour:** one `MeetingAttendance` Temporal workflow per employee per meeting (ID =
  `platform:meeting_id:employee_id`) supervises a bot pod from a warm pool. It uses heartbeats, relaunches the bot
  on a crash, and checkpoints the transcript every 5 s. Details: [digital-employee.md](digital-employee.md).
- **DB:** `meeting_db` (pg-ledger). **Events out:** `meeting.*`.

### 12. Realtime Voice
- **Purpose:** live listening and speaking in meetings. A Pipecat pipeline runs VAD → STT → diarization →
  addressing/turn-taking → response engine → speak policy → TTS.
- **Stateless workers** next to the bot pods, calling the GPU inference pool. Degrades to a CPU model, then to
  chat-only answers.

### 13. Workspace Manager
- **Purpose:** each employee's own laptop: a persistent Kata microVM with a desktop, IDE, toolchains and a home
  volume.
- **Owns:** workspaces, volumes, snapshots, live-view and takeover sessions.
- **Key behaviour:** starts when a task needs it, hibernates after 30 minutes idle, resumes in under 30 s, and
  rebuilds from the golden image when unhealthy. The Agent Runtime drives the in-VM workspace agent over mTLS.
- **DB:** `workspace_db` (pg-app).

### 14. Identity & Accounts
- **Purpose:** the employee's digital identity: email, calendar, chat, meeting, git and business-app accounts.
- **Key behaviour:** provisions and de-provisions through SCIM 2.0 and admin APIs. Credentials (passwords, TOTP
  seeds, keys) live only in OpenBao. Every account is marked as AI ([ADR-0010](adr/0010-ai-employees-are-disclosed.md)).
- **DB:** `identity_db` (pg-app). **Events out:** `identity.account_provisioned`, `identity.account_revoked`.

### 15. Evaluation Service
- **Purpose:** the release gate for employee behaviour. Runs per-role golden tasks, each employee's regression set
  and the safety suite (prompt injection, policy) against candidate brain or employee versions. Also runs
  shadow comparisons on live tasks.
- **Key behaviour:** no promotion if any metric regresses. Results are attached to `change_proposals` and
  `brain_versions`.
- **DB:** `eval_db` (pg-brain).

### 16. Web App / Public API
- **Web App:** Next.js frontend. Calls the public REST API through Envoy, the same API customers use, so there is
  no hidden backdoor API.
- **Public API:** REST/JSON with an OpenAPI spec, generated from the same protobuf contracts (gRPC-gateway).
  Provides webhooks for task completion. Mutating calls require an `Idempotency-Key` header.

---

## Platform services (per cell)

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

| Stream | Subjects | Producers | Main consumers |
|---|---|---|---|
| `TENANT` | `tenant.>` | Directory | all cell services (cache invalidation) |
| `WORKFORCE` | `employee.>` | Workforce | Agent Runtime (cache), Audit |
| `TASK` | `task.>`, `approval.>` | Task | Channel Gateway (notify), Audit, Web (via SSE) |
| `RUN` | `run.>` | Agent Runtime | Task (state), Brain (remember), Audit |
| `TOOL` | `tool.>` | Integration | Audit, Usage |
| `USAGE` | `usage.>`, `quota.>` | Model Gateway, Integration, Sandbox, Usage | Usage, Task, Model Gateway |
| `MEETING` | `meeting.>` | Meeting Service | Brain (meeting memory), Task (action items), Audit |
| `BRAIN` | `brain.>` | Brain Service | Audit, Evaluation Service, Workforce (version changes) |
| `DLQ` | `dlq.>` | (max-deliver advisories) | on-call alert + replay tool |
