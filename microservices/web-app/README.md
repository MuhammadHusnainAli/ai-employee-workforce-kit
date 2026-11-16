# Web App / Public API

| | |
|---|---|
| **Scope** | cell (served at the edge) |
| **Language** | TypeScript (Next.js) |
| **Database** | none |

## Purpose
Frontend for tenants and the public REST API (generated from the protobuf contracts). The web app uses the same public API customers use.

## Owns
- no business data (BFF only)

## API
- REST/JSON + OpenAPI via gRPC-gateway
- webhooks for task completion
- SSE for live task / transcript updates

## Events
| Direction | Subjects |
|---|---|
| In | `task.*` (SSE fan-out) · `meeting.transcript.segment` |
| Out | none |

## Depends on
- Envoy Gateway
- Keycloak (OIDC)
- all public APIs

## Scaling
Stateless; HPA.

## Failure behaviour
Static assets cached at the edge; API errors surface with retry guidance.

## Folder layout
```
web-app/
  app/
  components/
  lib/api-client/
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
