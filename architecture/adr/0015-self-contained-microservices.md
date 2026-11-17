# ADR-0015: Every microservice is a self-contained project

**Status:** Accepted (supersedes the shared platform libraries and root workspaces in the consequences of
[ADR-0013](0013-rust-and-python.md))

## Context
The first Phase 0 build used a monorepo workspace: a root `pyproject.toml` and `Cargo.toml`, shared platform
libraries under `libs/`, one shared `contracts/` folder, and templates and tools at the repo root. That couples
services through the build: one lockfile for all services, one dependency upgrade for all services, and a change to
a shared library rebuilds and redeploys everything that uses it. It also contradicts the spirit of
[principle 2](../README.md#1-design-principles) ("one writer per piece of data") for contracts, where one folder
held every service's API.

## Decision
- Each service folder under `microservices/` is a complete project that never points outside itself:
  - Python: `pyproject.toml`, `uv.lock`, `.python-version`. Rust: `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`.
  - Its **own copy** of the platform code: `src/wfk_platform/` (Python) or `src/platform/` (Rust): settings,
    telemetry, health, tenant-scoped transactions, outbox, inbox, NATS relay, migrations runner.
  - Its own `contracts/`: the common types (`wfk.common.v1`) copied in, and **its own API**
    (`wfk.<service>.v1`), which it owns. A caller copies the owner's proto to generate a client.
  - Its own `migrations/`, `justfile`, `Dockerfile` (build context is the service folder), `.dockerignore` and
    licence policy.
- There is no root project, lockfile or shared library. The repo root holds only docs, `deployments/` (the local
  stack) and a small `justfile` for the stack and `new-service`.
- Templates live in `microservices/templates/`. They are real, buildable services. `new_service.py` copies one and
  renames it. CI discovers every service folder and checks each one from inside it.
- Rust services apply migrations with sqlx, built into the binary (`<binary> migrate`). Python services use
  `wfk-migrate`. Both read the same `NNNN_name.up.sql` / `.down.sql` files and refuse edited migrations.

## Consequences
- A service builds, tests, upgrades and deploys on its own. One service's dependency bump never touches another.
- Platform code is duplicated. A platform fix goes into the template first, then into each service that needs it.
  The copies can drift, which is accepted in exchange for independence.
- The common contract types are copied, so a change to `wfk.common.v1` must be copied to each service. That
  package holds only infrastructure types and changes rarely; breaking changes need a `v2`.
- CI time grows with the number of services, since each one builds alone. Jobs run in parallel.
