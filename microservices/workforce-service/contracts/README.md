# Contracts

This service's own copy of its contracts. Code is generated from here (`just gen`); nothing generated is
hand-written.

```
proto/wfk/common/v1/          common types shared by every service (tenant context, errors, paging).
                              Copied into each service, never edited here: change it in the template.
proto/wfk/<this_service>/v1/  this service's own API. This service owns it; others copy it to call us.
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
