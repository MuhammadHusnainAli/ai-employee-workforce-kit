# Migrations

Every service starts with these two migrations in its own `migrations/` folder.

| File | What it does |
|---|---|
| `0001_platform_outbox_inbox` | The `outbox` and `inbox` tables used by `wfk-platform` in both languages |
| `0002_example_tenant_table` | The pattern for a tenant-owned table: `tenant_id` first in every index, RLS enabled and forced, grants to the service role. Delete it once the service has its own tables |

## Rules

- File names: `NNNN_snake_name.up.sql`, with an optional `NNNN_snake_name.down.sql`.
- Never edit a migration after it is applied anywhere. Both runners (`wfk-migrate` in Python services, sqlx in
  Rust services) store a checksum and refuse to run if it changed. Fix forward with a new migration.
- Expand, then contract: add the new column or table, ship code that uses both, then remove the old one in a later
  migration. A deploy must work against the schema before and after its own migration.
- Every tenant-owned table has `tenant_id uuid NOT NULL`, RLS enabled and forced, and the `tenant_isolation`
  policy.

## Commands

From the service folder:

```
just migrate          # apply pending migrations, creating the database if missing
just migrate-down     # Python services: revert the latest migration
```
