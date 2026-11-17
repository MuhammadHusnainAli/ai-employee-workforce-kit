# Integration & Tool Service

| | |
|---|---|
| **Scope** | cell |
| **Language** | Rust |
| **Database** | `integration_db` on `pg-app` |

## Purpose
Everything an employee can do in the outside world: the 9-stage tool call pipeline over MCP connectors.

## Owns
- connector registry (MCP servers, tool schemas, risk tiers, idempotency class)
- tenant connections (OAuth grants)
- tool_calls intent ledger

## API
- `AuthorizeTool`
- `ExecuteTool` (idempotency key `run_id:step:call_idx`)
- connection management (OAuth flows)

## Events
| Direction | Subjects |
|---|---|
| In | `identity.account_revoked` |
| Out | `tool.executed` · `tool.denied` · `usage.tool` |

## Depends on
- OpenFGA
- OpenBao
- egress proxy
- MCP connector servers

## Scaling
HPA on RPS; per-connector circuit breakers (state in Valkey).

## Failure behaviour
Fail closed without credentials; unknown outcomes go to reconcile() or a human, never a blind retry.

## Folder layout
```
integration-service/
  api/
  domain/pipeline/
  adapters/mcp/
  adapters/openfga/
  adapters/openbao/
  connectors/
  migrations/
  tests/fault-injection/
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
- Exposes `/healthz` (liveness) and `/readyz` (readiness), OTLP traces, metrics and logs.

## Architecture references
- [Service catalog](../../architecture/services.md)
- [ai-workforce-architecture.drawio (page 4)](../../architecture/diagrams/ai-workforce-architecture.drawio)
