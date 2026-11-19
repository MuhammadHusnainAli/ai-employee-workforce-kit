# Contracts

This service's contracts. Code is generated from here (`just gen`); nothing generated is
hand-written. Each module owns its API under the package name of the service it used to be
(workforce_service, agent_runtime, brain_service), so the wire contract did not change when the services became modules (ADR-0017).

```
proto/wfk/common/v1/          common types (tenant context, errors, paging). Infrastructure only.
proto/wfk/<module>/v1/        each module's own API. The module owns it; callers outside this service
                              copy it to call us.
proto/wfk/task_service/v1/    a copy of core-rust's task contract: the org tools create sub-tasks
                              through it. core-rust owns it; copy it again when it changes.
events/                       CloudEvents envelope and example events
```

## Rules

- **gRPC:** `buf lint` (STANDARD) and `buf breaking` (FILE) run in CI. A breaking change needs a new version
  package (`v2`), never an edit to `v1`.
- **Events:** every event is a CloudEvents 1.0 structured JSON message matching
  [events/cloudevent-envelope.schema.json](events/cloudevent-envelope.schema.json). The `type` equals the NATS
  subject (`run.completed`), and the `id` is a UUIDv7 that is also sent as `Nats-Msg-Id` so JetStream drops
  duplicates.
- `wfk.common.v1` holds infrastructure types only. Domain types belong to the service that owns them.

## Pinned-context cache (read by agent-runtime)

Every `Recall` writes the employee's pinned context to Valkey, so the agent runtime can fall back on it when
brain-api is unreachable (architecture/brain.md, section 3, step 6).

| | |
|---|---|
| Key | `wfk:brain:pinned:v1:<tenant_id>:<employee_id>` |
| Value | JSON `{"version": int, "identity": str, "rules": [str]}` |
| TTL | 24 hours, refreshed on every recall |

A breaking change to the value needs a new key version (`v2`), written alongside `v1` until readers move.
