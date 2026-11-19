# Channel Gateway

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | `channel_db` on `pg-app` |
| **Local port** | 7005 (HTTP webhooks and health, gRPC) |

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
  `ListBindings`, `DisableBinding`, `DeliverMessage` (post into a thread; idempotent). Secrets and tokens are never
  returned.

## Flows (src/flows.rs)
1. **Forward:** each stored message becomes a task through task-service `CreateTask`, idempotent on the message
   (`channel:<id>`), for the binding's employee. Retried with backoff while task-service is unavailable; a request
   it rejects is marked failed.
2. **Answer:** agent-runtime's run events become replies in the thread the task came from: the output on
   `run.completed`, a note when the run waits for a person or fails. A redelivered event replies once.
3. **Send:** each reply is posted the provider's way (Slack `chat.postMessage` with the bot token, Teams and Chat
   incoming webhooks, the mail relay and generic webhooks signed with `X-WFK-Signature`), retried with backoff,
   and recorded with its receipt. Slack's `{"ok": false}` counts as a failure.

The loops find work through id-only queues (`forward_queue`, `delivery_queue`) and do it in tenant transactions.

## Events
| Direction | Stream | Subjects |
|---|---|---|
| In | `RUNS` (consumer `channel-gateway-runs`) | `run.completed` · `run.failed` · `run.step_completed` (waiting for a person) |
| Out | `CHANNELS` | `channel.binding_created` · `channel.message_received` · `channel.message_delivered` · `channel.delivery_failed` |

## Depends on
- the task module (`CreateTask`, `WFK_TASK_SERVICE_ADDR`, default core-rust's own port)
- NATS JetStream (outbox relay, `RUNS` consumer)
- Google's Chat JWKS (`WFK_GOOGLE_CHAT_JWKS_URL`) for Google Chat bindings

## Scaling
HPA on RPS, ≥ 3 replicas.

## Failure behaviour
Provider message ID has a unique index: duplicate webhooks are no-ops. Outbound sends are idempotent activities.

## Folder layout (inside core-rust)
```
core-rust/
  src/channel/domain.rs    provider protocols: signatures, payloads, replies (no I/O)
  src/channel/adapters.rs  channel_db, Google's token check
  src/channel/flows.rs     message to task, run events to replies, sending with retries
  src/channel/api.rs       webhooks and ChannelService
  migrations/channel/      0001 outbox/inbox, 0002 bindings, hook_routes, inbound_messages, deliveries,
                           0003 work queues
  tests/                   hooks.rs: every provider's check, dedupe, tenancy; flows.rs: forwarding, answers,
                           retries, against a fake task-service and a fake provider
```

## Rules (every module)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../../../architecture/services.md)
