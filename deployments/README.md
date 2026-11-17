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
| PostgreSQL 18 + pgvector | `postgresql://wfk:wfk@127.0.0.1:7000/wfk` | `wfk` / `wfk` |
| Temporal (gRPC) | `127.0.0.1:7001`, namespace `default` | none |
| Temporal UI | <http://127.0.0.1:7002> | none |
| NATS JetStream | `nats://127.0.0.1:7003`, monitoring <http://127.0.0.1:7004> | none |
| Valkey | `redis://127.0.0.1:7005` | none |
| SeaweedFS (S3 API) | <http://127.0.0.1:7006>, buckets `wfk-artifacts`, `wfk-payloads`, `wfk-backups` | `wfk-local-access` / `wfk-local-secret` |
| Model Gateway (LiteLLM, OpenAI format) | <http://127.0.0.1:7007/v1>, models `local-mock` (no key), `claude-haiku`, `claude-sonnet` | bearer `sk-wfk-local`; provider keys in `deployments/.env` |
| Grafana (LGTM) | <http://127.0.0.1:7008> | `admin` / `admin` |
| OTLP | gRPC `127.0.0.1:7009`, HTTP `127.0.0.1:7010` | none |

## Port map (7000-7020)

Everything local listens on 127.0.0.1 in one range, so nothing collides with other projects on the machine. Each
service has exactly one port: Rust services serve HTTP `/healthz` and `/readyz` and gRPC on it; Python services serve
gRPC only, with health as the standard gRPC health service (`""` for liveness, `"readiness"` for readiness).

| Port | What |
|---|---|
| 7000 | PostgreSQL |
| 7001 / 7002 | Temporal gRPC / Temporal UI |
| 7003 / 7004 | NATS / NATS monitoring |
| 7005 | Valkey |
| 7006 | S3 object storage (SeaweedFS) |
| 7007 | Model Gateway (LiteLLM) |
| 7008 | Grafana |
| 7009 / 7010 | OTLP gRPC / OTLP HTTP |
| 7011 | workforce-service |
| 7012 | agent-runtime |
| 7013 | spare |
| 7014 | brain-service |
| 7015 | spare |
| 7016 | task-service |
| 7017 | integration-service |
| 7018 | audit-service |
| 7019 | usage-metering |
| 7020 | spare; also the templates' default |

A new service takes a free port and sets it in its settings (`port`) and its Dockerfile's `EXPOSE`. Stack ports can be
changed by copying `.env.example` to `.env` in this folder.

## Notes

- One PostgreSQL instance stands in for the production clusters (`pg-app`, `pg-ledger`, `pg-brain`,
  `pg-temporal`). Each service still gets its own database and role, created by its migrations.
- `vector`, `pg_trgm` and `pg_stat_statements` are installed in `template1`, so every new database has them.
- Object storage is SeaweedFS rather than MinIO, per [ADR-0007](../architecture/adr/0007-open-source-license-policy.md).
  Any S3 client works against it.
- Valkey runs without persistence on purpose: it holds only data that can be rebuilt.
- The Model Gateway runs without its database locally, so an unknown key gets 400 rather than 401, and
  there are no spend logs. Add `ANTHROPIC_API_KEY` to `deployments/.env` (git-ignored) for real model calls.
