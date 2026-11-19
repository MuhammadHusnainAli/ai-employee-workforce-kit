# Microservices

The backend is two services, one per language ([ADR-0017](../architecture/adr/0017-two-backend-services.md)), plus
the web app. Each capability from the architecture is a **module** inside the service of its language, with its
own database, migrations, contracts and tests. The architecture behind them is in
[../architecture](../architecture/README.md).

| Service | Language | Port | Modules |
|---|---|---|---|
| [core-python](core-python/README.md) | Python (+ Rust `brain-core` via PyO3) | 7001 | workforce, agent runtime, brain, evaluation; planned: model gateway, meeting, realtime voice, tenant directory, billing |
| [core-rust](core-rust/README.md) | Rust | 7002 | task, channel, sandbox; planned: integration, audit, usage metering, identity, workspace |
| [web-app](web-app/README.md) | TypeScript (Next.js) | 7000 | the frontend for tenants and the public REST API |

## Modules

| Module | Service | Scope | Database | Purpose |
|---|---|---|---|---|
| [Workforce](core-python/docs/modules/workforce.md) | core-python | cell | `workforce_db` | Defines AI employees: role, instructions, model policy, tool grants, knowledge bindings and guardrails, as immutable versions |
| [Agent Runtime](core-python/docs/modules/agent_runtime.md) | core-python | cell | `run_db` | Temporal workers that execute the `EmployeeRun` workflow and the agent harness: context, model, validate, act, observe |
| [Brain](core-python/docs/modules/brain.md) | core-python | cell | `brain_db` | Each employee's own brain: identity core, episodic memory, semantic graph, skills, preferences and rules, reflections, plus tenant knowledge |
| [Evaluation](core-python/docs/modules/evaluation.md) | core-python | cell | `eval_db` | Release gate for employee behaviour: golden tasks per role, per-employee regression sets, safety suites |
| [Task](core-rust/docs/modules/task.md) | core-rust | cell | `task_db` | The work inbox |
| [Channel](core-rust/docs/modules/channel.md) | core-rust | cell | `channel_db` | Inbound and outbound conversation channels: email, Slack, Microsoft Teams, Google Chat and webhooks |
| [Sandbox](core-rust/docs/modules/sandbox.md) | core-rust | cell | `sandbox_db` | Runs untrusted or generated code with strict limits: hardened containers locally, gVisor pods in production |
| [Model Gateway](core-python/docs/modules/model_gateway.md) (planned) | core-python | cell | `gateway_db` | The only path to any LLM: provider abstraction, fallback chains, budgets, token accounting, safe caching |
| [Meeting](core-python/docs/modules/meeting.md) (planned) | core-python | cell | `meeting_db` | Joins Zoom, Microsoft Teams and Google Meet as the employee; transcripts, notes, decisions and action items |
| [Realtime Voice](core-python/docs/modules/realtime_voice.md) (planned) | core-python | cell | none | Live listening and speaking in meetings (Pipecat) |
| [Tenant & Cell Directory](core-python/docs/modules/tenant_directory.md) (planned) | core-python | global | `directory_db` | Tenants, plans, feature flags and the tenant to cell routing table |
| [Billing](core-python/docs/modules/billing.md) (planned) | core-python | global | `billing_db` | Subscriptions, invoices and credit balances, fed by hourly usage rollups from every cell |
| [Integration & Tools](core-rust/docs/modules/integration.md) (planned) | core-rust | cell | `integration_db` | Everything an employee can do in the outside world: the 9-stage tool call pipeline over MCP connectors |
| [Audit](core-rust/docs/modules/audit.md) (planned) | core-rust | cell | `audit_db` | Immutable record of everything humans and employees did |
| [Usage Metering](core-rust/docs/modules/usage_metering.md) (planned) | core-rust | cell | `usage_db` | Counts LLM tokens, tool calls, sandbox seconds and storage per tenant and employee; enforces quotas |
| [Identity & Accounts](core-rust/docs/modules/identity.md) (planned) | core-rust | cell | `identity_db` | The employee's email, calendar, chat, meeting, git and business-app accounts, provisioned and removed automatically |
| [Workspace](core-rust/docs/modules/workspace.md) (planned) | core-rust | cell | `workspace_db` | Each employee's own laptop: a persistent Kata microVM with desktop, IDE, toolchains and a home volume |

Off-the-shelf platform components (Keycloak, Temporal, NATS JetStream, OpenFGA, OpenBao, Valkey, SeaweedFS,
LiteLLM base image, CloudNativePG) are not services we build; they are deployed from `/platform`.

## Service layout

Each service is a self-contained project ([ADR-0015](../architecture/adr/0015-self-contained-microservices.md)). It
never points outside its own folder.

```
core-<lang>/
  README.md        the service: modules, process, settings, commands
  pyproject.toml   + uv.lock (Python)    or    Cargo.toml + Cargo.lock + rust-toolchain.toml (Rust)
  justfile         lint, test, migrate, gen, image, ci: run from this folder
  Dockerfile       build context is this folder
  contracts/       proto/wfk/common/v1 + proto/wfk/<package>/v1 per module (its own API)
  migrations/      <module>/: versioned SQL per module database, expand then contract
  src/             one package (Python) or module (Rust) per module: api/, domain/, adapters/;
                   plus the platform code (wfk_platform/ or platform/), one copy per service
  tests/           unit, integration and contract tests, per module
  docs/modules/    one spec per module: purpose, API, events, database, scaling, failure behaviour
```

## Adding a capability

Build it as a module in the service of its language: a package under `src/`, its migrations under
`migrations/<module>/` (starting with the platform outbox and inbox schema), its API under
`contracts/proto/wfk/<package>/v1/`, and its spec in `docs/modules/`. Wire it into the service's composition
(`core_python/main.py` and `app.py`, or `core-rust/src/lib.rs`). `just new-service` and [templates/](templates/README.md)
are only for splitting a module out into its own service later.

## Rules every module follows

1. **One database.** No module reads another module's tables, even in the same process.
2. **Transactional outbox** for every event, **inbox** dedupe for every consumer.
3. **Deadlines** on every call, **idempotency keys** on every mutating call.
4. **Contracts first:** each module owns its protobuf API in its service's `contracts/` (`buf lint`), events as
   CloudEvents + AsyncAPI.
5. **Health and telemetry:** gRPC health or `/healthz`, `/readyz`, OTLP traces, metrics and logs with `trace_id`.
6. **Tenant isolation:** `SET LOCAL app.tenant_id` in every transaction; RLS enforced in the database.
