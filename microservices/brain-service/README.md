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
Built (`contracts/proto/wfk/brain_service/v1/brain.proto`):
- `Recall` (p95 < 300 ms)
- Brain Inspector: `ListMemories` (what the employee knows and why: trust, source, governance findings),
  `GetMemory` (history and episode), `CorrectMemory` (a human-verified version supersedes it),
  `DeleteMemory`, `ReviewMemory` (approve or reject quarantined memories)
- Remembering is asynchronous, from events (see Write path), not an RPC call

Later: `Feedback`, `Forget`, `GetBrain`, `ProposeChange` / `ApproveChange` / `Rollback`.

## Events
| Direction | Stream | Subjects |
|---|---|---|
| In | `RUNS` (consumer `brain-ingest-runs`) | `run.completed` · `run.failed` (later `meeting.notes_ready`, `feedback.given`, `channel.message_received`) |
| Out | `BRAIN` | `brain.memory_committed` (later `brain.version_promoted`, `brain.change_proposed`) |

## Write path (`brain-service ingest`, the `brain-ingest` deployable)
1. A finished run (`run.completed` / `run.failed`, carrying the task and output) starts `BrainIngest` on the
   Temporal queue `brain-ingest`; the workflow id comes from the event id, so a redelivery starts nothing new.
2. **Extract** (`WFK_EXTRACTOR`): `baseline` stores the episode only; `gateway` adds facts, preferences and
   lessons from `WFK_EXTRACTION_MODEL` through the Model Gateway, as structured output validated against a schema.
3. **Govern** (Rust, `brain-core/src/govern.rs`): secrets always removed, PII by tenant policy (`keep`,
   `redact`, `drop`; `tenant_policies`, default `WFK_PII_POLICY`), trust tag by origin. Text that tries to
   instruct the employee is stored as data and quarantined unless a person wrote it.
4. **Embed** all candidates in one batch, then **resolve and commit** in one transaction: reinforce
   near-duplicates, supersede an older value of the same `fact_key` (history kept), or quarantine a weaker
   contradiction for a human; `ingest_log` makes it idempotent and `brain.memory_committed` goes to the outbox.

Quarantined memories never reach recall until a person approves them.

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
A self-contained Python project ([ADR-0015](../../architecture/adr/0015-self-contained-microservices.md)), with
its Rust hot path (`brain-core`, PyO3) built inside it. The modules follow
[brain.md section 8](../../architecture/brain.md#8-building-the-brain-service); later phases add
`consolidate/` and `learn/`.

```
brain-service/
  pyproject.toml  uv.lock  justfile  Dockerfile
  brain-core/                Rust: recall ranking and the governance filter, exposed to Python with PyO3
  contracts/                 common types + this service's API (wfk.brain_service.v1)
  migrations/                brain_db
  evals/                     golden sets per role, regression and safety suites (data)
  src/brain_service/
    api/                     gRPC: Recall (later Remember, Feedback, Forget, Inspect, ...)
    domain/memory/           memories, episodes, identity core, brain versions (no I/O)
    domain/recall/           planner, retrievers, fusion, packer
    domain/write/            sources, extraction schema, governance, resolver (no I/O)
    remember.py              govern, embed in one batch, commit
    ingest.py                brain-ingest: Temporal worker, RUNS consumer, BRAIN relay
    adapters/postgres/       repositories, pgvector and full-text queries, the write commit
    adapters/modelgw/        embeddings and the extractor through the Model Gateway
    adapters/temporal/       the BrainIngest workflow and its activities
    adapters/cache/          Valkey identity-core cache
  src/wfk_platform/          this service's copy of the platform code
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
