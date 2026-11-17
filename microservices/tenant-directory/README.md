# Tenant & Cell Directory

| | |
|---|---|
| **Scope** | global |
| **Language** | Python |
| **Database** | `directory_db` on `pg-global` |

## Purpose
Source of truth for tenants, plans, feature flags and the tenant → cell routing table.

## Owns
- tenants, organisations, plans
- tenant → cell mapping
- per-tenant feature flags

## API
- `ResolveCell(tenant_id)` (served from memory, pushed to Envoy)
- tenant CRUD · plan changes · feature flags

## Events
| Direction | Subjects |
|---|---|
| In | `usage.rollup` (plan limits) |
| Out | `tenant.created` · `tenant.plan_changed` · `tenant.suspended` · `tenant.moved_cell` |

## Depends on
- Keycloak (organizations)
- Envoy Gateway (routing table push)

## Scaling
3 replicas, read-heavy, in-memory cache of the routing table.

## Failure behaviour
Envoy keeps the last-known routing table. Existing tenants keep working; only onboarding stops.

## Folder layout
```
tenant-directory/
  api/
  domain/
  adapters/
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
