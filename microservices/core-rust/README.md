# core-rust

The Rust backend: one self-contained crate and one binary holding the modules that used to be three services
([ADR-0017](../../architecture/adr/0017-two-backend-services.md)).

| | |
|---|---|
| **Language** | Rust (edition 2024, toolchain pinned in `rust-toolchain.toml`) |
| **Port** | 7002: HTTP (`/healthz`, `/readyz`, channel webhooks) and gRPC on the same listener |
| **Databases** | one per module: `task_db`, `channel_db`, `sandbox_db` |

## Modules

| Module | Path | What it does | Spec |
|---|---|---|---|
| Task | `src/task/` | the work inbox: tasks, run status, approvals, replies (`TaskService`); projects `run.*` events | [docs/modules/task.md](docs/modules/task.md) |
| Channel | `src/channel/` | Slack, Teams, Google Chat, email and webhooks to tasks and back (`ChannelService`, `/hooks/{kind}/{binding_id}`) | [docs/modules/channel.md](docs/modules/channel.md) |
| Sandbox | `src/sandbox/` | untrusted code in hardened containers, gVisor pods in production (`SandboxService`) | [docs/modules/sandbox.md](docs/modules/sandbox.md) |

Planned Rust modules (specs only, built here when their phase comes):

| Module | What it will do | Spec |
|---|---|---|
| Integration | the tool pipeline: policy, credentials, connectors, MCP (Phase 4) | [docs/modules/integration.md](docs/modules/integration.md) |
| Audit | append-only, hash-chained audit log (Phase 4) | [docs/modules/audit.md](docs/modules/audit.md) |
| Usage metering | tokens, tool calls and quotas on the hot path (Phase 4) | [docs/modules/usage_metering.md](docs/modules/usage_metering.md) |
| Identity | the employee's email, calendar, chat, git and app accounts, provisioned and removed (Phase 9) | [docs/modules/identity.md](docs/modules/identity.md) |
| Workspace | each employee's microVM "laptop" (Phase 9) | [docs/modules/workspace.md](docs/modules/workspace.md) |

Modules talk to each other the way they did as services: the channel module calls `TaskService` (on this
service's own port, `WFK_TASK_SERVICE_ADDR`), and events go through each module's outbox and inbox on NATS. A
module never reads another module's tables.

## One binary

`core-rust` serves every module's gRPC service, the webhooks and health on port 7002, and runs every module's
background loop: the task outbox relay (`TASKS`) and run inbox (`RUNS`); the channel outbox relay (`CHANNELS`),
forwarder, sender and run inbox; the sandbox outbox relay (`SANDBOX`). Each loop reconnects on its own, so the
service starts while NATS or a database is down, and `/readyz` reports every module's database.

## Settings (env prefix `WFK_`)

| Variable | Default | |
|---|---|---|
| `WFK_PORT` | `7002` | the one port |
| `WFK_TASK_DATABASE_URL` | `.../task_db` | |
| `WFK_CHANNEL_DATABASE_URL` | `.../channel_db` | |
| `WFK_SANDBOX_DATABASE_URL` | `.../sandbox_db` | |
| `WFK_TASK_SERVICE_ADDR` | `http://127.0.0.1:<port>` | where the channel module reaches `TaskService` |
| `WFK_SANDBOX_RUNTIME` | empty | `runsc` where gVisor is installed |
| `WFK_SANDBOX_SLOTS` | `4` | concurrent sandbox runs |
| `WFK_GOOGLE_CHAT_JWKS_URL` | Google's | keys for Google Chat tokens |

## Commands

```
just run              # every module against the local stack (just up at the repo root)
just migrate          # each module's migrations into its own database (core-rust migrate)
just ci               # fmt, clippy, unit + integration tests, contracts
core-rust healthcheck # exit 0 when ready (container health checks; the image has no shell)
```

## Folder layout

```
core-rust/
  Cargo.toml  Cargo.lock  rust-toolchain.toml  deny.toml  build.rs  justfile  Dockerfile
  contracts/proto/wfk/     common, task_service, channel_gateway, sandbox_runner (wire names unchanged)
  migrations/<module>/     one folder per module database, embedded in the binary
  src/
    lib.rs main.rs         the composed binary: settings, router, serve, migrate, healthcheck
    task/ channel/ sandbox/   one module each: mod.rs, api.rs, domain.rs, adapters.rs (channel: flows.rs)
    platform/              the platform code (one copy)
    contracts.rs           generated from contracts/ by build.rs
  images/                  the sandbox's allowlisted images
  tests/                   <module>_*.rs per module; platform_*.rs, contracts.rs and core.rs for the rest
  docs/modules/            one spec per module, built and planned
```
