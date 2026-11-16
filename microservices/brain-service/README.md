# Brain Service

| | |
|---|---|
| **Scope** | cell |
| **Language** | Python |
| **Database** | `brain_db` on `pg-brain` |
| **Deployables** | `brain-api` · `brain-ingest` · `brain-consolidate` · `brain-learn` |

## Purpose
Each employee's own brain: identity core, episodic memory, semantic graph, skills, preferences and rules, reflections, plus tenant knowledge. Recall, remember, consolidate and evolve.

## Owns
- memories
- entities / relations / memory_entities
- episodes
- skills
- policy_rules
- brain_versions
- change_proposals
- feedback
- recall_log
- documents / chunks (tenant knowledge)

## API
- `Recall` (p95 < 300 ms)
- `Remember` (async, idempotent)
- `Feedback`
- `Forget`
- `GetBrain`
- `ProposeChange` / `ApproveChange` / `Rollback`
- `Inspect` (Brain Inspector UI)

## Events
| Direction | Subjects |
|---|---|
| In | `run.completed` · `run.step_completed` · `meeting.notes_ready` · `feedback.given` · `channel.message_received` |
| Out | `brain.memory_committed` · `brain.version_promoted` · `brain.change_proposed` |

## Depends on
- Model Gateway (extraction, embeddings)
- Evaluation Service
- Temporal
- Valkey (recall cache)

## Scaling
Four deployables: brain-api (HPA), brain-ingest, brain-consolidate, brain-learn (KEDA).

## Failure behaviour
Recall degrades to cached identity + rules and never blocks a run; writes are idempotent; consolidation is resumable.

## Folder layout
```
brain-service/
  api/
  domain/memory/
  domain/recall/
  domain/write/
  domain/consolidate/
  domain/learn/
  domain/govern/
  adapters/postgres/
  adapters/modelgw/
  adapters/temporal/
  adapters/nats/
  adapters/cache/
  migrations/
  evals/
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
- [brain.md](../../architecture/brain.md)
