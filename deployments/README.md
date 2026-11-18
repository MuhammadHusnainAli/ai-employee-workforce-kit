# Deployments

`docker-compose.yml` runs the local development stack. Production deployment (Helm, cells, Argo CD) lives in
`/platform` and `/cells` later (plan.md, Phase 10).

```
just up        # start the stack (Postgres, Temporal, NATS, ...) and wait until healthy
just up-all    # the stack plus every microservice, built from its own Dockerfile
just ps        # status
just logs <service>      # follow one service, e.g. just logs agent-runtime-worker
just restart <service>   # rebuild and restart one service after a code change
just e2e       # end-to-end checks against the running containers
just down      # stop everything, keep data
just nuke      # stop everything and delete all data
```

## Microservices in containers

`just up-all` runs every implemented microservice from one place. They live in `docker-compose.yml` under the
`services` profile, so `just up` alone still starts only the stack and you can run one service from its folder
(`cd microservices/<service> && just run`) on its usual port.

| Container | What | Port |
|---|---|---|
| `workforce-service` | employees and versions (gRPC) | 7001 |
| `agent-runtime` | RunService, read API for runs (gRPC) | 7002 |
| `agent-runtime-worker` | Temporal workers, `run.*` relay, `task.*` inbox | none |
| `brain-service` | memory and recall (gRPC) | 7003 |
| `brain-ingest` | the brain's write path: finished runs become memories (Temporal queue `brain-ingest`) | none |
| `task-service` | tasks (gRPC) and HTTP `/healthz` `/readyz` | 7004 |
| `channel-gateway` | webhooks from Slack, Teams, Google Chat, email, webhooks to tasks and back | 7005 |
| `sandbox-runner` | untrusted code in hardened containers (mounts the Docker socket; runs as root locally only) | 7006 |
| `web-app` | the web app and public REST API (<http://127.0.0.1:7000>) | 7000 |
| `evaluation-service` | evaluates every proposed brain change (golden, regression and safety suites) | none |
| `agent-runtime-eval-worker` | agent-runtime on the evaluation queues, isolated from live work | none |

- Each service has a one-shot `<name>-migrate` container (same image) that applies its migrations and creates its
  database; the service starts only after it succeeds.
- Inside the network, services use the stack's internal addresses (`postgres:5432`, `nats:4222`, `temporal:7233`,
  `valkey:6379`, `seaweedfs:8333`, `model-gateway:4000`), set once in `x-service-env`.
- Health: Python services are probed through gRPC health (`readiness`), Rust services with `/service healthcheck`
  (the image is distroless: no shell, no curl).
- The worker uses the `local-mock` model. Set `WFK_WORKER_MODEL=claude-haiku` (and `ANTHROPIC_API_KEY`) in
  `deployments/.env` for a real model.
- A service whose folder has only a README (not built yet) has no container. A new service from
  `just new-service` comes with its Dockerfile; add its `-migrate` and service entries here when it is ready.

## Endpoints

| Component | Address | Credentials |
|---|---|---|
| PostgreSQL 18 + pgvector | `postgresql://wfk:wfk@127.0.0.1:7017/wfk` | `wfk` / `wfk` |
| Temporal (gRPC) | `127.0.0.1:7018`, namespace `default` | none |
| Temporal UI | <http://127.0.0.1:7019> | none |
| NATS JetStream | `nats://127.0.0.1:7020`, monitoring <http://127.0.0.1:7021> | none |
| Valkey | `redis://127.0.0.1:7022` | none |
| SeaweedFS (S3 API) | <http://127.0.0.1:7023>, buckets `wfk-artifacts`, `wfk-payloads`, `wfk-backups` | `wfk-local-access` / `wfk-local-secret` |
| Model Gateway (LiteLLM, OpenAI format) | <http://127.0.0.1:7024/v1>, models `local-mock` (no key), `claude-haiku`, `claude-sonnet` | bearer `sk-wfk-local`; provider keys in `deployments/.env` |
| Grafana (LGTM) | <http://127.0.0.1:7025> | `admin` / `admin` |
| OTLP | gRPC `127.0.0.1:7026`, HTTP `127.0.0.1:7027` | none |

## Port map (7000-7030)

Everything local listens on 127.0.0.1 in one range, so nothing collides with other projects on the machine: the web
app first, then the backends, then the infrastructure. Each service has exactly one port: Rust services serve HTTP
`/healthz` and `/readyz` and gRPC on it; Python services serve gRPC only, with health as the standard gRPC health
service (`""` for liveness, `"readiness"` for readiness).

| Port | What |
|---|---|
| 7000 | web-app: <http://127.0.0.1:7000> |
| 7001 | workforce-service |
| 7002 | agent-runtime |
| 7003 | brain-service |
| 7004 | task-service |
| 7005 | channel-gateway |
| 7006 | sandbox-runner |
| 7007 | integration-service (Phase 4) |
| 7008 | audit-service (Phase 4) |
| 7009 | usage-metering (Phase 4) |
| 7010 | identity-accounts (Phase 9) |
| 7011 | workspace-manager (Phase 9) |
| 7012 | meeting-service (Phase 9) |
| 7013 | realtime-voice (Phase 9) |
| 7014 / 7015 | spare |
| 7016 | spare; the templates' default (a new service picks its own) |
| 7017 | PostgreSQL |
| 7018 / 7019 | Temporal gRPC / Temporal UI |
| 7020 / 7021 | NATS / NATS monitoring |
| 7022 | Valkey |
| 7023 | S3 object storage (SeaweedFS) |
| 7024 | Model Gateway (LiteLLM) |
| 7025 | Grafana |
| 7026 / 7027 | OTLP gRPC / OTLP HTTP |
| 7028 to 7030 | spare |

Workers without a port: agent-runtime-worker, agent-runtime-eval-worker, brain-ingest, evaluation-service.

A new service takes a free backend port and sets it in its settings (`port`) and its Dockerfile's `EXPOSE`. Stack
ports can be changed by copying `.env.example` to `.env` in this folder.

## Notes

- One PostgreSQL instance stands in for the production clusters (`pg-app`, `pg-ledger`, `pg-brain`,
  `pg-temporal`). Each service still gets its own database and role, created by its migrations.
- `vector`, `pg_trgm` and `pg_stat_statements` are installed in `template1`, so every new database has them.
- Object storage is SeaweedFS rather than MinIO, per [ADR-0007](../architecture/adr/0007-open-source-license-policy.md).
  Any S3 client works against it.
- Valkey runs without persistence on purpose: it holds only data that can be rebuilt.
- The Model Gateway runs without its database locally, so an unknown key gets 400 rather than 401, and
  there are no spend logs. Add `ANTHROPIC_API_KEY` to `deployments/.env` (git-ignored) for real model calls.
