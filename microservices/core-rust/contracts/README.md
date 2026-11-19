# Contracts

This service's contracts. Code is generated from here (`build.rs`, on every build); nothing generated is
hand-written. Each module owns its API under the package name of the service it used to be
(task_service, channel_gateway, sandbox_runner), so the wire contract did not change when the services became modules (ADR-0017).

```
proto/wfk/common/v1/          common types (tenant context, errors, paging). Infrastructure only.
proto/wfk/<module>/v1/        each module's own API. The module owns it; callers outside this service
                              copy it to call us.
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
