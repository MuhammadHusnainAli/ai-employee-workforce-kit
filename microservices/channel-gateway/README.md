# Channel Gateway

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | `channel_db` on `pg-app` |
| **Local port** | 7013 (HTTP webhooks and health, gRPC) |

## Purpose
Inbound and outbound conversation channels: email, Slack, Microsoft Teams, Google Chat and webhooks. Turns external messages into tasks and task replies into messages.

## Owns
- channel bindings (inbox / channel → employee)
- inbound message log
- delivery receipts

## API
- Webhooks: `POST /hooks/{kind}/{binding_id}`, checked the provider's way (src/domain.rs):

  | kind | check |
  |---|---|
  | `slack` | `X-Slack-Signature` v0 HMAC over timestamp and body, 5 minute replay window; URL-verification challenge |
  | `teams` | outgoing webhook `Authorization: HMAC` (key: the base64 security token) |
  | `google_chat` | Google's RS256 bearer JWT: Chat's issuer, the app's project number as audience, keys from Google's JWKS |
  | `email` | `X-WFK-Signature: sha256=` HMAC from the mail relay |
  | `webhook` | the same `X-WFK-Signature` |

  Unknown, disabled or mismatched bindings answer 404; bad signatures 401. A message is stored once per
  binding and provider message id, so a webhook delivered twice is one message.
- gRPC `ChannelService` (`contracts/proto/wfk/channel_gateway/v1/channels.proto`): `CreateBinding` (idempotent),
  `ListBindings`, `DisableBinding`. Secrets and tokens are never returned.

## Events
| Direction | Stream | Subjects |
|---|---|---|
| Out | `CHANNELS` | `channel.binding_created` · `channel.message_received` |

## Depends on
- Task Service (`CreateTask`)
- Integration Service (outbound sends)

## Scaling
HPA on RPS, ≥ 3 replicas.

## Failure behaviour
Provider message ID has a unique index: duplicate webhooks are no-ops. Outbound sends are idempotent activities.

## Folder layout
```
channel-gateway/
  src/domain.rs     provider protocols: signatures, payloads, replies (no I/O)
  src/adapters.rs   channel_db, Google's token check
  src/api.rs        webhooks and ChannelService
  src/platform/     copied platform code
  migrations/       0001 outbox/inbox, 0002 bindings, hook_routes, inbound_messages, deliveries
  tests/            hooks.rs covers every provider's check, dedupe and tenancy
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../architecture/services.md)
