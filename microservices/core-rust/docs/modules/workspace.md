# Workspace Manager

> **Planned module of core-rust** (spec only, not built yet). Written as the `workspace-manager` service; under [ADR-0017](../../../../architecture/adr/0017-two-backend-services.md) it is built as a module inside core-rust, with its own database, migrations and contracts, and no port or container of its own.

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | `workspace_db` on `pg-app` |

## Purpose
Each employee's own laptop: a persistent Kata microVM with desktop, IDE, toolchains and a home volume, plus the in-VM workspace agent.

## Owns
- workspaces
- volumes and snapshots
- live-view / takeover sessions

## API
- `Provision` · `Start` · `Hibernate` · `Rebuild`
- `OpenLiveView` · `TakeOver` · `HandBack`
- workspace agent (in VM): exec, files, patch, browser, screenshot/input

## Events
| Direction | Subjects |
|---|---|
| In | `employee.created` · `employee.paused` |
| Out | `workspace.ready` · `workspace.hibernated` · `workspace.rebuilt` |

## Depends on
- Kubernetes (Kata RuntimeClass)
- CSI snapshots
- OpenBao (git credential helper)
- egress proxy

## Scaling
One VM per active employee; hibernate after 30 min idle, resume < 30 s.

## Failure behaviour
Unhealthy VM is rebuilt from the golden image and its volume reattached; nightly snapshots.

## Folder layout
```
workspace-manager/
  api/
  domain/
  adapters/k8s/
  agent/
  images/golden/
  migrations/
  tests/
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../../../architecture/services.md)
- [digital-employee.md](../../../../architecture/digital-employee.md)
