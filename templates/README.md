# Service templates

```
just new-service python <name>   # e.g. just new-service python workforce-service
just new-service rust <name>     # e.g. just new-service rust task-service
```

The command copies a template into `microservices/<name>/`, renames `service-template` / `service_template`, keeps
the service's existing spec (`README.md`), and registers it in the uv or cargo workspace.

Both templates are real workspace members, so CI builds, lints and tests them on every change. A template that
does not compile cannot be copied into a new service.

| | Python (`python-service/`) | Rust (`rust-service/`) |
|---|---|---|
| HTTP | FastAPI + uvicorn: `/healthz`, `/readyz` (Postgres check) | axum: `/healthz`, `/readyz` (Postgres check) |
| gRPC | `grpc.aio` with the standard health service | tonic with `tonic-health` |
| Platform | `wfk-platform` (settings, JSON logs, OTel, db, outbox, inbox) | `wfk-platform` crate (the same) |
| Database | Pool opens without waiting: the service starts while Postgres is down and `/readyz` reports it | Lazy pool, same behaviour |
| Shutdown | SIGINT/SIGTERM: gRPC health goes NOT_SERVING, servers drain | Ctrl-C/SIGTERM: same |
| Image | `python:3.13-slim`, non-root uid 10001 | distroless `cc-debian12:nonroot` |
| Migrations | `migrations/` from [migrations/](migrations/README.md) | same |

Build an image from the repo root (services need `libs/` and `contracts/`):

```
docker build -f microservices/<name>/Dockerfile -t <name> .
```
