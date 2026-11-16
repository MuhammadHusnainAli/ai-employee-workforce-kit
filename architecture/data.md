# Data Architecture (PostgreSQL)

Diagram: page **7 · Data & PostgreSQL** in [diagrams/ai-workforce-architecture.drawio](diagrams/ai-workforce-architecture.drawio).

## 1. Topology

There are a few **physical clusters** and many **logical databases**. Each service owns exactly one database and
one login role. Databases are grouped into clusters by **workload shape**, so a heavy workload cannot hurt a
latency-sensitive one.

| Cluster (CloudNativePG) | Scope | Databases | Workload shape |
|---|---|---|---|
| `pg-global` | once | `directory_db`, `keycloak_db`, `billing_db` | small, read-heavy, critical |
| `pg-app` | per cell | `task_db`, `workforce_db`, `channel_db`, `integration_db`, `gateway_db`, `openfga_db`, `workspace_db`, `identity_db` | OLTP, latency-sensitive |
| `pg-ledger` | per cell | `run_db`, `audit_db`, `usage_db`, `meeting_db` | append-heavy, partitioned, large |
| `pg-brain` | per cell | `brain_db` (employee brains: memories, graph, skills, rules, tenant knowledge), `eval_db` | the most critical data: vectors, graph, bi-temporal facts ([brain.md](brain.md)) |
| `pg-temporal` | per cell | `temporal`, `temporal_visibility` | high write rate, owned by Temporal |

Moving a database to its own cluster later takes logical replication and a connection-string change. No service
code changes, because no service ever crossed a database boundary.

## 2. High availability and durability (every cluster)

- **3 instances spread across 3 availability zones** (CNPG `topologySpreadConstraints`).
- **Synchronous quorum replication:** `synchronous: { method: any, number: 1 }`. A commit is acknowledged only after
  the primary and at least one replica have it. **RPO = 0** for an AZ or node loss.
- **Automated failover** by the CNPG operator, typically in under 30 seconds. Applications connect through the
  `-rw` service, which always points at the current primary.
- **PgBouncer** (CNPG `Pooler`) in **transaction mode**, separate `rw` and `ro` poolers. Read-only queries
  (dashboards, run history) go to replicas.
- **Backups:** continuous WAL archiving plus a daily base backup through the Barman Cloud plugin, stored in object
  storage in **another region**. Point-in-time recovery to any second within the retention window (35 days).
- **Restore drills:** a weekly automated job restores the latest backup into a scratch cluster, runs checksum and
  row-count checks, and alerts on failure. A backup you have never restored is not a backup.
- **DR:** a CNPG **replica cluster** in the DR region streams from the object store or by streaming replication.
  Promoting it is one manifest change (see [deployment.md](deployment.md)).

## 3. Multi-tenancy: shared schema with Row-Level Security

Every tenant-owned table has `tenant_id uuid NOT NULL`, and every table has this policy:

```sql
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks FORCE ROW LEVEL SECURITY;           -- applies to the table owner too
CREATE POLICY tenant_isolation ON tasks
  USING      (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
```

The service sets the tenant **inside each transaction**:

```sql
BEGIN;
SET LOCAL app.tenant_id = '0190f…';   -- SET LOCAL, not SET: safe with PgBouncer transaction pooling
...
COMMIT;
```

- The application role is **not** a superuser and does **not** have `BYPASSRLS`. If the code forgets the
  `WHERE tenant_id = …` clause, Postgres still returns only that tenant's rows.
- If `app.tenant_id` is unset, `current_setting` raises an error. A query that forgets the tenant fails loudly
  instead of leaking data.
- Cross-tenant jobs (metering rollups, migrations) use a separate role with `BYPASSRLS`, from a separate
  deployment.
- Large or regulated tenants can be placed in a **dedicated cell** with no code change (see deployment).
- An integration test suite runs every repository query as tenant A against tenant B's data and must find zero
  rows.

## 4. Conventions

- **Primary keys:** `uuid` generated with `uuidv7()` (native in PG 18). IDs sort by time, so b-tree indexes stay
  compact, and IDs are safe to expose in URLs.
- **Timestamps:** `timestamptz` only. **Money:** `numeric(20,6)` in micro-units. Never use float for money.
- **Optimistic concurrency:** a `version int` column, with `UPDATE … WHERE id = $1 AND version = $2`.
- **Soft delete only where the business needs it** (employees). Ledgers are never updated or deleted.
- **JSONB** for flexible, schema-light payloads (tool arguments, model settings), validated by the service against
  a JSON Schema before writing.
- **Partitioning:** `run_steps`, `audit_events`, `usage_events`, `outbox` and `inbox` are range-partitioned by month
  with **pg_partman**. Old partitions are detached and archived to object storage, not deleted with `DELETE`.
- **Migrations:** versioned SQL (goose for Go, Alembic for Python), applied by an Argo CD PreSync Job. Every change
  follows **expand → migrate → contract** across at least two releases, so the old and new versions of a service
  can run side by side during a rolling deploy. `CREATE INDEX CONCURRENTLY` only. `lock_timeout = '3s'` on
  migration sessions.

## 5. Reliable messaging tables (in every service database)

```sql
-- Transactional outbox: written in the SAME transaction as the business change
CREATE TABLE outbox (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),   -- becomes CloudEvent id + Nats-Msg-Id
  tenant_id     uuid NOT NULL,
  subject       text NOT NULL,                       -- e.g. 'task.created'
  payload       jsonb NOT NULL,
  traceparent   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  published_at  timestamptz
);
CREATE INDEX outbox_unpublished ON outbox (created_at) WHERE published_at IS NULL;

-- Inbox: makes consumers idempotent (exactly-once *effect* on top of at-least-once delivery)
CREATE TABLE inbox (
  event_id     uuid PRIMARY KEY,
  consumer     text NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now()
);
```

**Relay** (one per service, leader-elected through a Postgres advisory lock):
`SELECT … FROM outbox WHERE published_at IS NULL ORDER BY created_at LIMIT 100 FOR UPDATE SKIP LOCKED` →
publish with `Nats-Msg-Id = id` → wait for the JetStream ack → `UPDATE … SET published_at = now()`. If the relay
crashes between the publish and the update, the next attempt re-publishes and JetStream's dedupe window removes the
duplicate.

**Consumer:** `BEGIN; INSERT INTO inbox … ON CONFLICT DO NOTHING;` If the insert affected 0 rows, the event was
already handled → `ACK` and stop. Otherwise apply the change, `COMMIT`, then `ACK`.

## 6. Key tables (abridged)

```sql
-- workforce_db
employees          (id, tenant_id, name, status[active|paused|archived], active_version_id, created_at, version)
employee_versions  (id, tenant_id, employee_id, version_no, role, instructions text,
                    model_policy jsonb, tool_grants jsonb, knowledge_bindings uuid[],
                    guardrails jsonb  -- {max_steps, max_cost_usd, max_duration, approval_tiers}
                    , created_by, created_at)                               -- immutable

-- task_db
tasks              (id, tenant_id, employee_id, employee_version_id, parent_task_id, source[api|channel|schedule|delegation],
                    title, input jsonb, state, priority, idempotency_key UNIQUE(tenant_id, idempotency_key),
                    result_ref, created_at, updated_at, version)
approvals          (id, tenant_id, task_id, run_step, tool, risk_tier, request jsonb,
                    status[pending|approved|rejected|expired], decided_by, decided_at, expires_at)

-- run_db (partitioned)
runs               (id=task_id, tenant_id, employee_version_id, started_at, ended_at, status, total_tokens, total_cost)
run_steps          (run_id, step_no, tenant_id, kind[think|tool|approval|memory], input_ref, output_ref,
                    tokens_in, tokens_out, cost, latency_ms, error, created_at)  PRIMARY KEY (run_id, step_no, created_at)

-- integration_db
connections        (id, tenant_id, connector, secret_path /* OpenBao path, never the secret */, scopes, status)
tool_calls         (idempotency_key PRIMARY KEY, tenant_id, run_id, tool, args jsonb, status[started|succeeded|failed|unknown],
                    result_ref, provider_request_id, started_at, finished_at)

-- brain_db (full model: brain.md and diagram page 11)
memories           (id, tenant_id, employee_id NULL=shared, scope, kind[episodic|semantic|preference|lesson|reflection],
                    content, embedding vector(1024), tsv, importance, confidence, trust, source_type, source_id,
                    valid_from, valid_to, recorded_at, superseded_by, status)
entities / relations / memory_entities   -- semantic graph (adjacency tables, recursive CTE ≤ 2 hops)
episodes · skills · policy_rules · brain_versions · change_proposals · feedback · recall_log
documents / chunks                        -- tenant-scope knowledge base
  -- HNSW on embeddings (vector_cosine_ops); GIN on tsv

-- audit_db (append-only, partitioned; role has INSERT+SELECT only)
audit_events       (id, tenant_id, occurred_at, actor_type[user|employee|system], actor_id, action, target,
                    details jsonb, trace_id, prev_hash bytea, hash bytea)
```

## 7. What Valkey and SeaweedFS are allowed to hold

- **Valkey:** rate-limit counters, short-lived caches (blueprint cache, JWKS, LLM response cache), SSE fan-out.
  Rule: `FLUSHALL` at any moment must cause slowness, never incorrect behaviour or lost data.
- **SeaweedFS (S3):** uploaded documents, large prompts and completions (claim-check), sandbox inputs and outputs,
  audit exports (object lock), Postgres backups (a different bucket and region). Postgres stores only the object
  key and its checksum.
