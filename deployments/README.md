# Deployments

`docker-compose.yml` runs the local development stack. Production deployment (Helm, cells, Argo CD) lives in
`/platform` and `/cells` later (plan.md, Phase 10).

```
just up        # start the stack (Postgres, Temporal, NATS, ...) and wait until healthy
just up-all    # the stack plus both backend services and the web app, each from its own Dockerfile
just ps        # status
just logs <service>      # follow one service, e.g. just logs core-python
just restart <service>   # rebuild and restart one service after a code change
just e2e       # end-to-end checks against the running containers
just down      # stop everything, keep data
just nuke      # stop everything and delete all data
```

## Microservices in containers

`just up-all` runs the backend from one place: two services, one per language
([ADR-0017](../architecture/adr/0017-two-backend-services.md)), and the web app. They live in
`docker-compose.yml` under the `services` profile, so `just up` alone still starts only the stack and you can run a
service from its folder (`cd microservices/<service> && just run`) on its usual port.

| Container | What | Port |
|---|---|---|
| `core-python` | one process: WorkforceService, RunService and BrainService (gRPC); the agent's Temporal workers (live queues, and evaluation queues apart); brain ingest and consolidation; evaluations; every Python outbox relay and inbox | 7001 |
| `core-rust` | one binary: TaskService, ChannelService and SandboxService (gRPC), channel webhooks and HTTP `/healthz` `/readyz`; every Rust outbox relay and inbox. Mounts the Docker socket for the sandbox and runs as root, locally only | 7002 |
| `web-app` | the web app and public REST API (<http://127.0.0.1:7000>) | 7000 |

- Each service has a one-shot `<name>-migrate` container (same image) that applies every module's migrations to
  that module's own database (creating it if missing); the service starts only after it succeeds. Module
  databases: `workforce_db`, `run_db`, `brain_db`, `eval_db` (core-python) and `task_db`, `channel_db`,
  `sandbox_db` (core-rust).
- Inside the network, services use the stack's internal addresses (`postgres:5432`, `nats:4222`, `temporal:7233`,
  `valkey:6379`, `seaweedfs:8333`, `model-gateway:4000`), set once: `x-service-env` for both services and
  `x-python-env` for what only core-python reads. core-rust waits only for Postgres and NATS.
- Health: core-python is probed through gRPC health (`readiness`, which checks every module's database),
  core-rust with `/service healthcheck` (the image is distroless: no shell, no curl).
- The agent uses the `local-mock` model. Set `WFK_WORKER_MODEL=claude-haiku` (and `ANTHROPIC_API_KEY`) in
  `deployments/.env` for a real model.
- New capabilities are built as modules inside the service of their language, so they need no new container.

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
app first, then the backends, then the infrastructure. Each service has exactly one port: core-rust serves HTTP
`/healthz` and `/readyz`, the webhooks and gRPC on it; core-python serves gRPC only, with health as the standard
gRPC health service (`""` for liveness, `"readiness"` for readiness).

| Port | What |
|---|---|
| 7000 | web-app: <http://127.0.0.1:7000> |
| 7001 | core-python (workforce, agent runtime, brain, evaluation, and later Python modules) |
| 7002 | core-rust (task, channel, sandbox, and later Rust modules) |
| 7003 to 7016 | spare (free since ADR-0017) |
| 7017 | PostgreSQL |
| 7018 / 7019 | Temporal gRPC / Temporal UI |
| 7020 / 7021 | NATS / NATS monitoring |
| 7022 | Valkey |
| 7023 | S3 object storage (SeaweedFS) |
| 7024 | Model Gateway (LiteLLM) |
| 7025 | Grafana |
| 7026 / 7027 | OTLP gRPC / OTLP HTTP |
| 7028 to 7030 | spare |

A service split out of a core service later takes a free backend port and sets it in its settings (`port`) and its
Dockerfile's `EXPOSE`. Stack
ports can be changed by copying `.env.example` to `.env` in this folder.

## Notes

- One PostgreSQL instance stands in for the production clusters (`pg-app`, `pg-ledger`, `pg-brain`,
  `pg-temporal`). Each module still gets its own database, created by its service's migrate step.
- `vector`, `pg_trgm` and `pg_stat_statements` are installed in `template1`, so every new database has them.
- Object storage is SeaweedFS rather than MinIO, per [ADR-0007](../architecture/adr/0007-open-source-license-policy.md).
  Any S3 client works against it.
- Valkey runs without persistence on purpose: it holds only data that can be rebuilt.
- The Model Gateway runs without its database locally, so an unknown key gets 400 rather than 401, and
  there are no spend logs. Add `ANTHROPIC_API_KEY` to `deployments/.env` (git-ignored) for real model calls.
