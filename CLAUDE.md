# CLAUDE.md

Instructions for AI coding agents (and humans) working in this repository. Read this first, then
[plan.md](plan.md) for what is being built now.

## What this repo is

The AI Employee Workforce Kit: a platform for AI employees (digital workers with their own role, brain, accounts
and laptop). The design is in [architecture/](architecture/README.md); decisions are ADRs in
[architecture/adr/](architecture/adr/README.md). Work happens phase by phase, following [plan.md](plan.md).

## Hard rules

1. **The backend is two self-contained services: `core-python` and `core-rust`**
   ([ADR-0017](architecture/adr/0017-two-backend-services.md), [ADR-0015](architecture/adr/0015-self-contained-microservices.md)).
   - Each has its own `pyproject.toml` + `uv.lock`, or `Cargo.toml` + `Cargo.lock` + `rust-toolchain.toml`, one
     copy of the platform code (`src/wfk_platform/` in Python, `src/platform/` in Rust), and its own
     `contracts/`, `migrations/`, `justfile`, `Dockerfile`.
   - A service never points outside its own folder (no `../` paths in manifests, includes or imports).
   - **A new capability is a module, not a service:** a package in `core-python/src/core_python/<module>/` or a
     module in `core-rust/src/<module>/`, with its spec in `docs/modules/<module>.md`. A module owns one
     database (`migrations/<module>/`) and its API (`contracts/proto/wfk/<package>/v1/`), and never reads
     another module's tables; it talks to other modules over their gRPC contracts and NATS events.
   - **Never** add a `pyproject.toml`, `Cargo.toml`, lockfile, `libs/`, `contracts/`, `templates/` or `tools/` at
     the repo root. The root holds only docs, `deployments/`, `plan.md` and a small `justfile`.
2. **Two languages: Rust and Python** ([ADR-0013](architecture/adr/0013-rust-and-python.md)). No Go. Rust for
   throughput-heavy modules (in `core-rust`), Python for Temporal workflows and LLM-facing modules (in
   `core-python`). All Temporal workflows are Python.
3. **`agent-harness-adk` is a PyPI dependency, nothing more** ([ADR-0016](architecture/adr/0016-harness-package-as-dependency.md)).
   It is in `core-python`'s `pyproject.toml` (`agent-harness-adk>=0.2,<0.3`). Never clone, fork,
   branch or modify that package from here. Build on its public API.
4. **One task, one commit.** After each task in `plan.md`, commit with a 5 to 10 word imperative message, normally
   the one listed for the task. Do not add a `Co-Authored-By` trailer (a git hook rejects it). `main` holds the
   latest code; merge work into it at the end of each phase. Do not push unless asked.
5. **Local ports stay in 7000-7030, one port per service:** web app 7000, `core-python` 7001, `core-rust` 7002,
   infrastructure 7017-7030. The port map is in `deployments/README.md`. `core-rust` serves HTTP health and gRPC
   on its one port; `core-python` serves gRPC only, with gRPC health. Modules get no port of their own. Never add
   a port outside the range.
6. **No em dashes** in any doc, diagram, README or comment you write.
7. **Done means verified.** A task is done when its checks pass: tests, lint, types. Report failures honestly.
8. **Ask before anything hard to undo or public:** pushing, publishing packages, deleting data you did not create.

## Layout

```
architecture/                 design docs, ADRs, draw.io diagram
deployments/docker-compose.yml   local stack: Postgres 18 + pgvector, Temporal, NATS, Valkey, SeaweedFS,
                                 LiteLLM, Grafana. All local ports are in 7000-7030 (deployments/README.md)
microservices/
  core-python/                the Python backend: workforce, agent_runtime, brain, evaluation, collaboration (port 7001)
  core-rust/                  the Rust backend: task, channel, sandbox modules (port 7002)
  web-app/                    the web app and public REST API (port 7000)
plan.md                       phased plan and status
justfile                      local stack only
```

## Commands

From the repo root:

```
just up                          # start the local stack and wait until healthy
just up-all                      # the stack plus core-python, core-rust and the web app in containers
just e2e                         # end-to-end checks against those containers
just down                        # stop it (keeps data);  just nuke  deletes data
```

From a service folder (`cd microservices/core-python` or `cd microservices/core-rust`):

```
just ci                # everything CI checks: lint, types, unit + integration tests, contracts, licences
just test              # unit tests only (no docker)
just test-integration  # needs: just up
just lint / just fmt
just migrate           # apply every module's migrations, each to its own local database
just gen               # Python: regenerate src/wfk from contracts/ (Rust does it in build.rs)
```

## Conventions

- **Python:** 3.13, uv, ruff (line length 100), pyright standard mode, pytest with `asyncio_mode = auto`.
  Integration tests are marked `@pytest.mark.integration`. On Windows, async psycopg needs a selector event loop;
  the service's `conftest.py` handles it.
- **Rust:** edition 2024, `unsafe_code = "forbid"`, clippy with `-D warnings`, rustfmt. Integration tests are
  `#[ignore = "needs the local stack: just up"]` and run with `cargo test -- --ignored`. Keep the service's own
  crate imports in a separate `use` block so rustfmt does not reorder them by name.
- **Service code layout:** `api/` (transport), `domain/` (logic, no I/O), `adapters/` (Postgres, NATS, Temporal,
  other services). Platform code holds infrastructure only, never domain logic.
- **Data:** each module owns one database. Every tenant-owned table has `tenant_id`, RLS enabled and forced, and
  the `tenant_isolation` policy. Use `Database.tenant_tx(tenant_id)`. Events go through the outbox in the same
  transaction as the state change.
- **Migrations:** `NNNN_name.up.sql` + `.down.sql`. Never edit an applied migration; add a new one. Expand, then
  contract.
- **Contracts:** each module owns its API in `contracts/proto/wfk/<package>/v1/` (package names keep the former
  service names, such as `wfk.brain_service.v1`, so the wire contract is stable). `wfk.common.v1` only holds
  infrastructure types.
- **Tests:** test the failure cases, not only the happy path (rollback publishes nothing, redelivery runs once,
  other tenants are invisible). Use `FakeProvider` from `agent_harness` for anything that calls a model.
- **Licences:** only OSI-approved dependencies ([ADR-0007](architecture/adr/0007-open-source-license-policy.md)).
  Python: `just licenses`. Rust: `just licenses` (needs `cargo-deny`).
- **Changing a decision:** write a new ADR that supersedes the old one. Do not edit accepted ADRs.

## Where things are going

See [plan.md](plan.md). The agent step loop lives in
`microservices/core-python/src/core_python/agent_runtime/harness/`, on top of `agent-harness-adk`.
