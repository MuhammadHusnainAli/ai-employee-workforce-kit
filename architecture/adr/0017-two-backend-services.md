# ADR-0017: Two backend services, one per language: core-python and core-rust

**Status:** Accepted (supersedes the deployable split of [ADR-0001](0001-service-boundaries.md) and the
one-project-per-service rule of [ADR-0015](0015-self-contained-microservices.md))

## Context
[ADR-0001](0001-service-boundaries.md) cut the platform into 16 cell services plus 3 global ones, and
[ADR-0015](0015-self-contained-microservices.md) made each one a self-contained project. Seven are built (four in
Python, three in Rust) and ten more exist as specs. For the team building this today, that costs more than it buys:

- Four identical copies of the Python platform code and three of the Rust one, kept in step by hand.
- Seven lockfiles, seven images, seven migrate jobs, eleven containers locally, and proto files copied between
  services so they can call each other.
- Every new capability starts with `new-service`, a port and a compose entry, before any of its own code exists.

The boundaries themselves (who owns which data, which events flow where) have held up well. The cost is in the
number of deployables, not in the boundaries.

## Decision
- The backend is **two services**, one per language ([ADR-0013](0013-rust-and-python.md) still holds):
  - **core-python** (`microservices/core-python`, port 7001): the workforce, agent runtime, brain and evaluation
    modules, and every later Python capability (model gateway plugins, meetings, realtime voice, tenant directory,
    billing).
  - **core-rust** (`microservices/core-rust`, port 7002): the task, channel and sandbox modules, and every later
    Rust capability (integration, audit, usage metering, identity, workspace manager).
- The web app stays its own deployable. It is not backend.
- Each former service becomes a **module** inside its language's service. ADR-0001's boundaries become module
  boundaries, and they keep the rules that made them work:
  - Each module **owns its own database** (`workforce_db`, `run_db`, `brain_db`, `eval_db`, `task_db`,
    `channel_db`, `sandbox_db`), with its own migrations folder. A module never reads another module's tables.
  - Modules talk through their existing contracts: gRPC (on the service's own port, or a direct call inside the
    Rust binary) and NATS events through each module's outbox and inbox. Proto package names (`wfk.<service>.v1`)
    do not change, so callers and stored events are unaffected.
  - Code layout per module stays `api/`, `domain/`, `adapters/`.
- **Python runs as one process**: the gRPC server, every Temporal worker, every inbox and every outbox relay.
  Evaluation runs still use their own task queues (`eval-runs`, `eval-llm`, `eval-tools`), so they never take
  live work. Background parts are supervised: one failing part is restarted without stopping the rest.
- **Rust runs as one binary**: one HTTP and gRPC port, every module's routes and services, every relay.
- ADR-0015's project rules now apply to these two services: each is self-contained (own manifest, lockfile,
  platform code, contracts, migrations, justfile, Dockerfile) and never points outside its folder. Nothing is
  built at the repo root.
- The ten unbuilt services become module specs in the service of their language
  (`docs/modules/<module>.md`). They are built there when their phase comes.

## Consequences
- One copy of the platform code per language, one lockfile, one image and one migrate step per service.
- A module's dependency upgrade now upgrades the whole service. Modules share a release cadence.
- Modules share a failure domain and resources: a memory leak in one module affects the others in the same
  process. Supervision covers failed loops, not exhausted resources. Live and evaluation work still use separate
  Temporal queues.
- Scaling is per service, not per module. If one module needs to scale or fail on its own later, it is split out
  again along its module boundary: its database, contracts and events are already separate, which is the cheap
  direction ADR-0001 called for.
- `just new-service` and the templates stay for that case only. New capabilities start as modules.
