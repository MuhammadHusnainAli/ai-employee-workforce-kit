# Contracts

Language-neutral contracts between services. Code is generated from here for Rust and Python; nothing in this folder
is hand-written per language.

```
proto/wfk/<domain>/v1/   gRPC contracts (package wfk.<domain>.v1)
events/                  CloudEvents envelope and AsyncAPI documents per service
```

## Rules

- **gRPC:** `buf lint` (STANDARD) and `buf breaking` (FILE) run in CI. A breaking change needs a new version
  package (`v2`), never an edit to `v1`.
- **Events:** every event is a CloudEvents 1.0 structured JSON message matching
  [events/cloudevent-envelope.schema.json](events/cloudevent-envelope.schema.json). The `type` equals the NATS
  subject (`run.completed`), and the `id` is a UUIDv7 that is also sent as `Nats-Msg-Id` so JetStream drops
  duplicates.
- `wfk.common.v1` holds infrastructure types only: tenant context, idempotency keys, error details, pagination.
  Domain types belong to the service that owns them.

## Commands

```
just contracts-lint       # buf lint
just contracts-breaking   # buf breaking against main
just gen                  # generate Rust and Python code
```
