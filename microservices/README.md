# Microservices

One folder per microservice. Each folder is an independent deployable with its own database, migrations and tests.
The architecture behind these services is in [../architecture](../architecture/README.md).

| Service | Scope | Language | Database | Purpose |
|---|---|---|---|---|
| [Tenant & Cell Directory](tenant-directory/README.md) | global | Go | `directory_db` on `pg-global` | Source of truth for tenants, plans, feature flags and the tenant → cell routing table |
| [Billing](billing/README.md) | global | Go | `billing_db` on `pg-global` | Subscriptions, invoices and credit balances, fed by hourly usage rollups from every cell |
| [Channel Gateway](channel-gateway/README.md) | cell | Go | `channel_db` on `pg-app` | Inbound and outbound conversation channels: email, Slack, Microsoft Teams, Google Chat and webhooks |
| [Task Service](task-service/README.md) | cell | Go | `task_db` on `pg-app` | The work inbox |
| [Workforce Service](workforce-service/README.md) | cell | Go | `workforce_db` on `pg-app` | Defines AI employees: role, instructions, model policy, tool grants, knowledge bindings and guardrails, as immutable versions |
| [Agent Runtime (harness)](agent-runtime/README.md) | cell | Python | `run_db` on `pg-ledger` | Temporal workers that execute the `EmployeeRun` workflow and the agent harness: context → model → validate → act → observe |
| [Model Gateway](model-gateway/README.md) | cell | Python (LiteLLM proxy + plugins) | `gateway_db` on `pg-app`; rate limits and cache in Valkey | The only path to any LLM: provider abstraction, fallback chains, budgets, token accounting, safe caching |
| [Integration & Tool Service](integration-service/README.md) | cell | Go | `integration_db` on `pg-app` | Everything an employee can do in the outside world: the 9-stage tool call pipeline over MCP connectors |
| [Sandbox Runner](sandbox-runner/README.md) | cell | Go | none (stateless; inputs/outputs are S3 refs) | Runs untrusted or generated code in short-lived gVisor pods with strict limits |
| [Brain Service](brain-service/README.md) | cell | Python | `brain_db` on `pg-brain` | Each employee's own brain: identity core, episodic memory, semantic graph, skills, preferences and rules, reflections, plus tenant knowledge |
| [Audit Service](audit-service/README.md) | cell | Go | `audit_db` on `pg-ledger` (role has INSERT + SELECT only) | Immutable record of everything humans and employees did |
| [Usage Metering](usage-metering/README.md) | cell | Go | `usage_db` on `pg-ledger` | Counts LLM tokens, tool calls, sandbox seconds and storage per tenant and employee; enforces quotas |
| [Meeting Service](meeting-service/README.md) | cell | Python | `meeting_db` on `pg-ledger` | Joins Zoom, Microsoft Teams and Google Meet as the employee; keeps transcripts; produces notes, decisions and action items |
| [Realtime Voice](realtime-voice/README.md) | cell | Python (Pipecat) | none (stateless) | Live listening and speaking in meetings: VAD → STT → diarization → addressing / turn-taking → response engine → speak policy → TTS |
| [Workspace Manager](workspace-manager/README.md) | cell | Go | `workspace_db` on `pg-app` | Each employee's own laptop: a persistent Kata microVM with desktop, IDE, toolchains and a home volume, plus the in-VM workspace agent |
| [Identity & Accounts](identity-accounts/README.md) | cell | Go | `identity_db` on `pg-app` (secrets only in OpenBao) | The employee's digital identity: email, calendar, chat, meeting, git and business-app accounts, provisioned and removed automatically |
| [Evaluation Service](evaluation-service/README.md) | cell | Python | `eval_db` on `pg-brain` | Release gate for employee behaviour: golden tasks per role, per-employee regression sets, safety suites and shadow comparisons |
| [Web App / Public API](web-app/README.md) | cell (served at the edge) | TypeScript (Next.js) | none | Frontend for tenants and the public REST API (generated from the protobuf contracts) |

Off-the-shelf platform components (Keycloak, Temporal, NATS JetStream, OpenFGA, OpenBao, Valkey, SeaweedFS,
LiteLLM base image, CloudNativePG) are not microservices we build; they are deployed from `/platform`.

## Standard layout

```
<service>/
  api/          transport layer (gRPC / REST handlers, request validation)
  domain/       business logic and invariants; no I/O
  adapters/     Postgres, NATS, Temporal, external systems
  migrations/   versioned SQL, expand → contract
  tests/        unit, integration and contract tests
  README.md     the service spec: purpose, API, events, database, scaling, failure behaviour
```

Services with special needs extend this (for example `workflows/` and `harness/` in agent-runtime, `connectors/`
in integration-service, `evals/` in brain-service).

## Rules every service follows

1. **One database, one role.** No service reads another service's tables.
2. **Transactional outbox** for every event, **inbox** dedupe for every consumer.
3. **Deadlines** on every call, **idempotency keys** on every mutating call.
4. **Contracts first:** protobuf in `/contracts` (CI runs `buf breaking`), events as CloudEvents + AsyncAPI.
5. **Health and telemetry:** `/healthz`, `/readyz`, OTLP traces, metrics and logs with `trace_id`.
6. **Tenant isolation:** `SET LOCAL app.tenant_id` in every transaction; RLS enforced in the database.
