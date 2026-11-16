# ADR-0005: Shared-schema tenancy with RLS; cells for isolation and scale

**Status:** Accepted

## Context
Options: a database per tenant (strong isolation, but thousands of databases to migrate and pool), a schema per
tenant (same problem), or a shared schema (efficient, but one missing `WHERE` clause can leak data).

## Decision
- Shared schema with `tenant_id` on every row, protected by PostgreSQL `FORCE ROW LEVEL SECURITY` using
  `SET LOCAL app.tenant_id` per transaction (compatible with PgBouncer transaction mode).
- Above this, a **cell architecture**. Tenants are assigned to cells, each a full independent stack. Large or
  regulated tenants get dedicated cells.

## Consequences
- Isolation enforced by the database, not by developer discipline. Tests check for cross-tenant leaks.
- Blast radius is limited to one cell. Scaling means adding cells.
- A global Tenant & Cell Directory and edge routing are required. Moving a tenant between cells is a workflow.
