# Model Gateway

| | |
|---|---|
| **Scope** | cell |
| **Language** | Python (LiteLLM proxy + plugins) |
| **Database** | `gateway_db` on `pg-app`; rate limits and cache in Valkey |

## Purpose
The only path to any LLM: provider abstraction, fallback chains, budgets, token accounting, safe caching.

## Owns
- model routing config
- spend logs
- budgets per tenant / employee / run

## API
- OpenAI-compatible chat / embeddings API (internal only)

## Events
| Direction | Subjects |
|---|---|
| In | `quota.exceeded` |
| Out | `usage.llm` (tokens, cost, latency) |

## Depends on
- LLM providers (via egress)
- self-hosted vLLM
- Valkey

## Scaling
HPA on RPS; ≥ 3 replicas.

## Failure behaviour
Fallback across providers; retries only on 429/5xx; budget hard stop before the provider call.

## Folder layout
```
model-gateway/
  config/
  plugins/
  migrations/
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
