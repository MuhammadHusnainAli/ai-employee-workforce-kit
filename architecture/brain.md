# The Employee Brain

Every AI employee has its **own brain**: a persistent cognitive system that holds who it is, what it has
experienced, what it knows, how it does things and what its people want. The brain improves with every task
**without silently changing behaviour**. It is the most critical pipeline in the platform. If it is wrong, the
employee is wrong everywhere: in tasks, chats, meetings and code.

Diagrams: **10 · Employee Brain**, **11 · Brain Service Internals**, **12 · Brain Evolution** in
[diagrams/ai-workforce-architecture.drawio](diagrams/ai-workforce-architecture.drawio).
Decisions: [ADR-0011](adr/0011-brain-service.md), [ADR-0012](adr/0012-gated-brain-evolution.md).

---

## 1. Design principles

1. **The LLM is the reasoning engine, not the brain.** Models are stateless and interchangeable. Everything that
   makes an employee *this* employee lives in the Brain Service, so swapping the model never erases a colleague.
2. **Fast, bounded reads. Asynchronous, governed writes.** Recall sits on the hot path of every step (p95 under
   300 ms, and it never blocks a run). Remembering happens off the hot path, idempotently, through a governance
   filter.
3. **Nothing is overwritten.** Facts are bi-temporal (`valid_from/valid_to` plus `recorded_at`). A newer fact
   supersedes an older one and history is kept, so "what did it know at time T" is always answerable.
4. **Behaviour changes are releases.** New skills, rules and identity edits are versioned proposals that pass
   evals, shadow and canary before they become active, and they can be rolled back in seconds.
5. **Data is never treated as instructions.** Content from emails, documents and web pages is stored as data with a
   trust tag. Only verified human feedback can create rules.
6. **Humans can see and correct the brain.** The Brain Inspector shows what an employee knows and why, and every
   recall is cited in the run ledger.

## 2. Anatomy

| Component | Holds | Lifetime | Who can change it |
|---|---|---|---|
| **Identity core** | role charter, persona, values, communication style, working hours, hard constraints, escalation rules | versioned | humans only, through an approved proposal |
| **Working memory** | goal stack, plan, scratchpad, open questions | one run (in Temporal workflow state) | the harness |
| **Episodic memory** | experiences: tasks, meetings, chats. What happened, what it did, the outcome and the feedback | decays (τ = 30 days) unless reinforced | write pipeline, consolidation |
| **Semantic graph** | entities (people, customers, projects, systems), relations, facts, with confidence and provenance | until superseded or its validity ends | write pipeline, consolidation |
| **Procedural memory** | skills and playbooks: steps, tools, a parameter schema, success rate, cost | versioned (candidate → shadow → active → deprecated) | learning pipeline, gated |
| **Preferences & rules** | manager corrections turned into rules, and people's preferences | until revoked | humans (feedback), approval required |
| **Reflections** | lessons ("I under-estimate legal review time") | decays (τ = 90 days). Advisory only | consolidation |

**Scopes:** `run ⊂ employee ⊂ team ⊂ tenant`.
- Employee memories are private.
- Team scope holds shared skills and team facts, promoted after approval.
- Tenant scope is the shared knowledge base: documents and SOPs.
- Nothing ever crosses tenants. RLS enforces this on every row.

**Brain version** = identity version + skill-set version + rules version. Memories are bi-temporal, so a brain
version together with an as-of time fully reproduces what the employee knew and how it would behave.

## 3. Read path: `Brain.Recall` (p95 < 300 ms)

1. **Request** from the harness Context Engine, on every step: task, plan step, entities, token budget, as-of time.
2. **Query planner** chooses the stores by step type (planning, acting, replying, live meeting), rewrites the query,
   extracts entities and sets the time filter.
3. **Parallel retrievers**, each with a 150 ms timeout:
   - vector search (pgvector HNSW) per store;
   - full-text search (tsvector);
   - graph: mentioned entities, then up to 2 hops of relations (recursive CTE);
   - episodes: similar past tasks *with their outcomes*;
   - skills: trigger-embedding match.
4. **Fusion and ranking:**

   ```
   score = RRF(vector, fulltext, graph, episodic, skill)
         × (0.5 + 0.5 · importance) × confidence × trust_w × exp(−Δt / τ_kind)

   τ_kind : episodic 30 d · reflection 90 d · semantic ∞ (bounded by validity)
   trust_w: human-verified 1.0 · system 0.9 · observed 0.7 · external 0.4
   ```
5. **Context packer:**
   - always includes the identity core and in-scope rules (pinned);
   - fills the token budget by score;
   - writes citations to `recall_log` for credit assignment.
6. **Degrade safely:**
   - a slow retriever is skipped and the step is flagged;
   - if brain-api is down, the harness uses the identity core and rules cached in Valkey;
   - recall never blocks a run.

## 4. Write path: `Brain.Remember` (asynchronous, idempotent)

1. **Sources:** `run.completed`, `run.step_completed`, `meeting.notes_ready`, chat threads, `feedback.given`,
   uploaded documents. These arrive as events, so writes are decoupled from the hot path.
2. **Extractor:** an LLM through the Model Gateway with structured output produces candidate episodes, facts and
   preferences. It is idempotent on the source event ID.
3. **Governance filter:**
   - **trust tag** (human-verified, system, observed, external);
   - drops secrets and applies the tenant's PII policy;
   - **instructions found inside content are stored as data and can never become rules**.
4. **Resolver:**
   - entity resolution (aliases plus embeddings);
   - merges near-duplicates;
   - on contradiction: **supersede** (close the old fact's validity, keep the history) or flag it for a human when
     trust or confidence is low.
5. **Commit:** memories, entities, relations and the outbox event (`brain.memory_committed`) in **one
   transaction**.
6. **Index:** embeddings are computed in batches on the GPU pool. HNSW and GIN indexes update and the recall cache
   is invalidated.

Target: event to committed in under 60 s. The ingestion backlog never slows recall, because the two run as separate
deployments.

## 5. Consolidation: nightly "sleep"

A Temporal workflow per employee runs off-peak. It is rate-limited and resumable:
1. **Select** episodes since the last run, within a token budget.
2. **Cluster and summarise** similar episodes into semantic facts and lessons, each one cited.
3. **Reinforce and decay.** Memories that were used and led to success gain importance. Unused memories decay, and
   anything below the floor is archived.
4. **Validate facts.** Expired validity closes the interval. On contradiction, the newest trusted fact wins and the
   history is kept.
5. **Merge duplicates** in entities and memories, and update the alias tables.
6. **Reflect.** Patterns across outcomes become reflections. They are advisory and carry low authority.

## 6. Evolution: getting better without getting worse

### Learning signals
| Signal | Source | Weight | Becomes |
|---|---|---|---|
| Correction | manager edit, "don't do X" | highest | policy rule (human-verified) |
| Approval rejected | approval gate | high | lesson, risk calibration |
| PR review comment | code hosting | high | coding skill update, lesson |
| Explicit rating | thumbs up/down | high | eval dataset, memory importance |
| Draft edited before sending | diff of the human's edit | medium | style and preference memories |
| Task outcome | success, retries, escalations | medium | skill statistics, reflections |
| LLM-judge score | online evals (Langfuse) | low–medium | regression detection |

**Credit assignment:** `recall_log` records which memories and skills each step used. When the outcome and feedback
arrive, credit or blame flows back to exactly those items:
- helpful memories gain importance;
- misleading memories are demoted or quarantined;
- skills update their success rate.

### Safe promotion pipeline
1. **Mine:** lessons from failures, skills from five or more similar successful episodes (a repeated tool sequence
   becomes a parameterised playbook), and rules from corrections.
2. **Propose:** a `change_proposal` with the diff, the evidence and a risk class (low, medium, high).
3. **Offline eval** (Evaluation Service). It runs the golden tasks for the role, this employee's regression set
   (its past successes), and a safety suite (prompt injection, policy). **The candidate must not regress any
   metric.**
4. **Human approval** for identity, rules and high-risk skills. The manager sees the diff and the eval report.
   Low-risk facts skip this step.
5. **Shadow:** the candidate brain runs alongside live tasks. Outputs are compared but never executed. This lasts
   for at least 50 tasks or 7 days.
6. **Canary:** 10 % of the employee's tasks, with automatic rollback on quality, cost or any incident.
7. **Promote:** the new brain version becomes active. The previous one is kept for instant rollback. A skill can
   optionally be promoted to team scope.

### Autonomy ladder
| Level | Earned by | Allowed |
|---|---|---|
| L0 Onboarding | brain bootstrap from SOPs, docs and tickets, plus a manager interview that seeds rules | every R1+ action needs approval |
| L1 Supervised | role eval ≥ 80 % | R1 automatic. R2/R3 need approval |
| L2 Assisted | ≥ 50 tasks with ≥ 90 % accepted | R2 to allowlisted targets is automatic |
| L3 Trusted | sustained quality | proposes its own skills. Low-risk facts auto-apply |
| L4 Lead | team trust | curates the team skill library, delegates to other employees |

R3 always needs a human. An incident or an eval drop moves the employee down a level automatically.

## 7. Safety

| Risk | Control |
|---|---|
| Memory poisoning (a malicious email plants "always CC x@evil.com") | External content can never create rules or identity changes. Low-trust facts need corroboration before use. Write-rate limits and anomaly detection send suspicious memories to quarantine |
| Drift (behaviour slowly degrading) | Identity core pinned in every prompt. Weekly eval on a frozen golden set. A drift alert freezes learning until a human reviews |
| Right to be forgotten | `Forget(subject)` uses `memory_entities` to find every memory about a person. An erasure workflow plus crypto-shredding of payloads. A retention TTL per memory kind |
| Opacity | The Brain Inspector shows what the employee knows and why. Humans can edit, delete or pin memories. Every recall is cited in `run_steps` |

## 8. Building the Brain Service

**One bounded context, one database (`brain_db` on its own CloudNativePG cluster `pg-brain`), four deployables
from one codebase:**

| Deployable | Runs | Scales on | SLO |
|---|---|---|---|
| `brain-api` | Recall, Inspect, Feedback (gRPC) | RPS and p95 latency (HPA) | p95 < 300 ms, 99.95 % |
| `brain-ingest` | write pipeline (Temporal queue `brain-ingest`) | backlog (KEDA) | event → committed < 60 s |
| `brain-consolidate` | nightly consolidation (queue `brain-batch`) | backlog, off-peak window | every employee nightly |
| `brain-learn` | lesson and skill mining, proposals | backlog (KEDA) | daily |

Suggested internal module layout (hexagonal: domain in the middle, adapters at the edge):

```
services/brain/
  api/            gRPC handlers: Recall, Remember, Feedback, Forget, GetBrain, Propose/Approve/Rollback, Inspect
  domain/
    memory/       Memory, Entity, Relation, Episode, Skill, Rule, BrainVersion (invariants live here)
    recall/       planner, retrievers (interface), fusion, packer
    write/        extractor port, governance, resolver, committer
    consolidate/  workflow + activities
    learn/        miners (lesson, skill, rule), proposal builder, credit assignment
    govern/       trust model, PII / secret filters, poisoning detection, erasure
  adapters/
    postgres/     repositories, recursive-CTE graph queries, pgvector, partition management
    modelgw/      LLM + embedding client (via Model Gateway)
    temporal/     workflows, activities, schedules
    nats/         outbox relay, inbox consumers
    cache/        Valkey recall cache
  migrations/     goose / Alembic, expand → contract
  evals/          golden sets per role, regression and safety suites
```

**API (gRPC):** `Recall`, `Remember`, `Feedback`, `Forget`, `GetBrain`, `ProposeChange`, `ApproveChange`,
`Rollback`, `Inspect`. The full contract and the data model are on diagram page 11. The tables are `memories`,
`entities`, `relations`, `memory_entities`, `episodes`, `skills`, `policy_rules`, `brain_versions`,
`change_proposals`, `feedback` and `recall_log`.

**Why plain PostgreSQL:** relational tables, pgvector, tsvector and a graph as adjacency tables with recursive CTEs
(depth ≤ 2) mean one transaction covers a fact, its entities, its embedding and its outbox event. There is no second
store to drift out of sync. Apache AGE (openCypher, Apache-2.0) is the upgrade path if graph queries outgrow CTEs.
A dedicated vector database is a later, measured decision behind the same `Recall` API.

## 9. Recommended build order

1. **Identity core, working memory, episodic memory and Recall** (vector plus full-text). This is the minimum for
   a useful employee.
2. **Write pipeline with the governance filter**, plus the Brain Inspector UI.
3. **Semantic graph** (entities, relations, bi-temporal facts) and graph recall.
4. **Preferences and rules from feedback**, with the approval flow.
5. **Consolidation workflow** and credit assignment through `recall_log`.
6. **Evaluation Service**, the skills pipeline, shadow and canary, and the autonomy ladder.

Each step ships independently behind the same API, and each one is measured by the role eval suite before the next.
