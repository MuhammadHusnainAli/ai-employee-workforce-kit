# Deployments

`docker-compose.yml` runs the local development stack. Production deployment (Helm, cells, Argo CD) lives in
`/platform` and `/cells` later (plan.md, Phase 10).

```
just up      # start and wait until healthy, then create S3 buckets
just ps      # status
just down    # stop, keep data
just nuke    # stop and delete all data
```

## Endpoints

| Component | Address | Credentials |
|---|---|---|
| PostgreSQL 18 + pgvector | `postgresql://wfk:wfk@127.0.0.1:5432/wfk` | `wfk` / `wfk` |
| Temporal (gRPC) | `127.0.0.1:7233`, namespace `default` | none |
| Temporal UI | <http://127.0.0.1:8233> | none |
| NATS JetStream | `nats://127.0.0.1:4222`, monitoring <http://127.0.0.1:8222> | none |
| Valkey | `redis://127.0.0.1:6379` | none |
| SeaweedFS (S3 API) | <http://127.0.0.1:8333>, buckets `wfk-artifacts`, `wfk-payloads`, `wfk-backups` | `wfk-local-access` / `wfk-local-secret` |
| Model Gateway (LiteLLM, OpenAI format) | <http://127.0.0.1:4000/v1>, models `local-mock` (no key), `claude-haiku`, `claude-sonnet` | bearer `sk-wfk-local`; provider keys in `deployments/.env` |
| Grafana (LGTM) | <http://127.0.0.1:3001> | `admin` / `admin` |
| OTLP | gRPC `127.0.0.1:4317`, HTTP `127.0.0.1:4318` | none |

Ports can be changed by copying `.env.example` to `.env` in this folder.

## Notes

- One PostgreSQL instance stands in for the production clusters (`pg-app`, `pg-ledger`, `pg-brain`,
  `pg-temporal`). Each service still gets its own database and role, created by its migrations.
- `vector`, `pg_trgm` and `pg_stat_statements` are installed in `template1`, so every new database has them.
- Object storage is SeaweedFS rather than MinIO, per [ADR-0007](../architecture/adr/0007-open-source-license-policy.md).
  Any S3 client works against it.
- Valkey runs without persistence on purpose: it holds only data that can be rebuilt.
- The Model Gateway runs without its database locally, so an unknown key gets 400 rather than 401, and
  there are no spend logs. Add `ANTHROPIC_API_KEY` to `deployments/.env` (git-ignored) for real model calls.
