# Web App / Public API

|                |                                   |
| -------------- | --------------------------------- |
| **Scope**      | cell (served at the edge)         |
| **Language**   | TypeScript (Next.js 16, React 19) |
| **Database**   | none                              |
| **Local port** | 7020                              |

## Purpose

Frontend for tenants and the public REST API. The web app uses the same public API customers use.

## Owns

- no business data (backend for frontend only)

## API

REST/JSON at `/api/v1`, described by `contracts/openapi.json` (served at `/api/v1/openapi.json`). The document is
the contract: `tests/contract.test.ts` fails when a route exists without it or it lists a route that does not exist.

|                    |                                                                                                                                                  |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Tasks              | `GET/POST /tasks`, `GET /tasks/{id}` (with its run), `GET /tasks/{id}/steps`, `POST /tasks/{id}/cancel`, `/approval`, `/reply`                   |
| Live updates       | `GET /tasks/stream`: server-sent events (`event: task`) for your tenant, from the `TASKS` stream                                                 |
| Brain Inspector    | `GET /employees/{employee}/memories`, `GET/DELETE /memories/{id}`, `POST /memories/{id}/correct`, `/review`                                      |
| Feedback and rules | `POST /employees/{employee}/feedback`, `GET /employees/{employee}/proposals`, `/rules`, `POST /proposals/{id}/decide`, `POST /rules/{id}/revoke` |

- Writes that create something or answer a run need an `Idempotency-Key` header; a retry with the same key changes
  nothing twice.
- Errors are `{"error": {code, message, retryable}}` with the matching status (400, 401, 403, 404, 409, 429, 503,
  504); retryable ones carry `Retry-After`.
- Every backend call has a 5 s deadline.
- `/healthz` (liveness) and `/readyz` (task-service, agent-runtime and brain-service reachable).

## Authentication

Production puts OIDC (Keycloak) in front: not wired yet. Locally (`WFK_AUTH_MODE=dev`, the default) every request
acts as one tenant (`WFK_DEV_TENANT`) and `X-WFK-Actor` names the person; set `WFK_API_TOKEN` to require
`Authorization: Bearer <token>` as well.

## Events

| Direction | Stream                                                    | Subjects                         |
| --------- | --------------------------------------------------------- | -------------------------------- |
| In        | `TASKS` (an ordered consumer per open stream, from "now") | `task.*` for the caller's tenant |

## Depends on

|                      | Setting                  | Default                 |
| -------------------- | ------------------------ | ----------------------- |
| task-service         | `WFK_TASK_SERVICE_ADDR`  | `127.0.0.1:7016`        |
| agent-runtime (runs) | `WFK_RUN_SERVICE_ADDR`   | `127.0.0.1:7012`        |
| brain-service        | `WFK_BRAIN_SERVICE_ADDR` | `127.0.0.1:7014`        |
| NATS                 | `WFK_NATS_URL`           | `nats://127.0.0.1:7003` |

The contracts are this app's copies (`contracts/proto`), like every other caller.

## Scaling

Stateless; HPA.

## Failure behaviour

A backend that is down gives 503 with `Retry-After`; the live stream sends an `error` event and closes, and the
browser reconnects.

## Folder layout

```
web-app/
  app/api/v1/        route handlers (thin: caller, input, handler)
  app/healthz, readyz
  lib/api/           handlers over the backends, HTTP plumbing, the live stream
  lib/api-client/    gRPC clients from contracts/proto
  contracts/         openapi.json (the public contract), proto copies
  tests/             vitest: handlers on fake backends, the contract
  tools/             licence check
```

```
just sync   just run   just ci   just image
```

## Architecture references

- [Service catalog](../../architecture/services.md)
