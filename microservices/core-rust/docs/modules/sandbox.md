# Sandbox Runner

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | `sandbox_db`: no business data; one row per execution (idempotency) and the outbox |
| **Local port** | 7006 (HTTP health and gRPC) |

## Purpose
Runs untrusted or generated code in short-lived gVisor pods with strict limits.

## Owns
- nothing persistent

## API
- gRPC `SandboxService.RunSandbox` (`contracts/proto/wfk/sandbox_runner/v1/sandbox.proto`): an allowlisted image
  (`images/README.md`), code and stdin (64 KiB each), limits; returns exit code, stdout and stderr (256 KiB each),
  whether it timed out or ran out of memory. Idempotent on the key: a retry returns the first outcome and never runs
  the code twice.

## What every run gets
| | Locally (Docker, `bollard`) | Production (Kubernetes) |
|---|---|---|
| Isolation | Docker's runtime, or `runsc` (gVisor) when `WFK_SANDBOX_RUNTIME=runsc` and installed | `runtimeClassName: gvisor` |
| Network | none (`network_mode: none`) | none (NetworkPolicy denies pods labelled `wfk.io/sandbox`) |
| Filesystem | read-only root; `/tmp` tmpfs 64 MB, `noexec,nosuid,nodev` | read-only root; `/tmp` memory `emptyDir` |
| Privilege | user 65534, all capabilities dropped, `no-new-privileges` | `runAsNonRoot`, drop ALL, no escalation, seccomp RuntimeDefault, no service account token |
| Limits | memory (no swap), CPU, pids, wall clock (killed at the deadline) | the same as resources and `activeDeadlineSeconds` |

The pod spec is `domain::Spec::pod` (unit-tested); this repository has no cluster to run it on. `WFK_SANDBOX_SLOTS`
(default 4) caps concurrent runs per replica.

## Events
| Direction | Stream | Subjects |
|---|---|---|
| Out | `SANDBOX` | `usage.sandbox` (one per execution, through the outbox) |

## Depends on
- A container runtime: the Docker API locally (the compose container mounts the Docker socket), Kubernetes in
  production

## Scaling
Pod per execution on a tainted node pool; KEDA on queue backlog.

## Failure behaviour
Hard CPU/memory/time limits, no network by default, read-only root FS.

## Folder layout (inside core-rust)
```
core-rust/
  src/sandbox/domain.rs    image allowlist, limits, the launcher, the gVisor pod spec (no I/O)
  src/sandbox/adapters.rs  the Docker runner, the executions store
  src/sandbox/api.rs       SandboxService
  images/                  the allowlisted images
  migrations/sandbox/      0001 outbox/inbox, 0002 executions
  tests/sandbox.rs         real runs: no network, read-only root, non-root, no capabilities, time, memory, pids
```

## Rules (every module)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../../../architecture/services.md)
