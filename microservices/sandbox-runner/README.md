# Sandbox Runner

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | none (stateless; inputs/outputs are S3 refs) |

## Purpose
Runs untrusted or generated code in short-lived gVisor pods with strict limits.

## Owns
- nothing persistent

## API
- `RunSandbox(image, code_ref, inputs_ref, limits)` → outputs_ref

## Events
| Direction | Subjects |
|---|---|
| In | (Temporal activities on the `sandbox` queue) |
| Out | `usage.sandbox` |

## Depends on
- Kubernetes (gVisor RuntimeClass)
- SeaweedFS

## Scaling
Pod per execution on a tainted node pool; KEDA on queue backlog.

## Failure behaviour
Hard CPU/memory/time limits, no network by default, read-only root FS.

## Folder layout
```
sandbox-runner/
  api/
  domain/
  adapters/k8s/
  images/
  tests/
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../architecture/services.md)
