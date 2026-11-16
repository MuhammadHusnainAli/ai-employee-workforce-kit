# Reliability

"Unbreakable" does not mean nothing fails. It means every failure has a **known, bounded, tested outcome**, and
none of those outcomes is a lost task, a duplicated action or a cross-tenant leak.

## 1. Service level objectives

| SLO | Target | Measured as |
|---|---|---|
| Public API availability | 99.9 % / 30 days | non-5xx responses / all responses at Envoy |
| Task acceptance latency | p99 < 300 ms | `CreateTask` returning 202 |
| Run start latency | p99 < 2 s | `task.created` → first workflow task executed |
| **Task durability** | **100 %**: no accepted task is ever lost | daily reconciliation: every task has a workflow; every workflow has a terminal state or is open < its max duration |
| **Duplicate side effects** | **0** | `tool_calls` unique-key violations that reached a provider (should be impossible) |
| Data loss on AZ failure | RPO = 0 | synchronous quorum replication |
| Regional disaster | RPO ≤ 5 min, RTO ≤ 1 h | WAL archive lag; quarterly DR drill |

Error budgets gate releases. When a service has spent more than 50 % of its budget, it ships only reliability
fixes until it recovers.

## 2. Failure-mode table

| What fails | Impact | Why it is contained | Recovery |
|---|---|---|---|
| Agent Runtime pod crashes mid-run | none visible | Workflow state is in Temporal. Another worker picks up the task after the activity's heartbeat timeout | automatic, seconds |
| LLM provider outage or 429 storm | slower runs | Model Gateway falls back to the next provider. Activities retry with backoff. Run queues back up but nothing is lost | automatic. KEDA adds workers when the provider recovers |
| All LLM providers down | runs pause | Activities keep retrying under their retry policy (up to hours). Tasks stay `RUNNING`, then the timeout moves them to `WAITING_HUMAN` | automatic resume |
| Tool / SaaS API down | that tool fails | Per-connector circuit breaker. The run gets a "tool unavailable" observation and can choose another path or wait | automatic |
| Worker crash during a non-idempotent tool call | possible unknown outcome | `tool_calls` intent row, then reconcile or ask a human (see runtime-flow.md) | human decision, never a blind retry |
| Postgres primary dies | 10–30 s of write errors on that cluster | CNPG promotes a synchronous replica (no data loss). Services retry with backoff. Temporal retries on its own | automatic |
| Whole availability zone lost | reduced capacity | 3-AZ spread for Postgres, NATS, Temporal, OpenBao and all Deployments (topology spread + PDBs) | automatic. HPA/KEDA rebalance |
| NATS node lost | none | streams `replicas: 3`, quorum continues | automatic |
| NATS cluster fully down | events delayed, **not lost** | Outbox rows accumulate in each service DB. User requests still succeed. Runs continue, because Temporal does not depend on NATS | relay drains the backlog on recovery |
| Temporal down | new runs and steps wait | Tasks are still accepted (outbox holds the "start run" command). Running activities finish and their completion is retried | backlog drains on recovery |
| Valkey lost | higher latency | Ephemeral data only. Rate limiting falls back to a local in-process limiter | automatic |
| OpenBao sealed or down | tool calls needing credentials fail | Runs retry the activity. Nothing is executed without a credential, so the system fails closed | auto-unseal via KMS |
| Keycloak down | no **new** logins | JWT validation is local (cached JWKS). Existing sessions and API tokens keep working until expiry | restore Keycloak |
| Tenant floods tasks (noisy neighbour) | only that tenant slows | Envoy per-tenant rate limit, Temporal per-tenant fairness keys, per-tenant concurrency caps in the runtime | automatic |
| Runaway agent (loop, cost spike) | bounded cost | `max_steps`, `max_cost`, `max_duration` checked every iteration, plus the Model Gateway budget hard stop | employee paused, human notified |
| Bad deploy | limited to one cell's canary | Argo Rollouts canary on one cell first, analysis on error rate and latency, automatic rollback. Workflow versioning protects in-flight runs | automatic rollback |
| Poison event | one consumer stalls on one message | `max_deliver = 10` → message moves to the `DLQ` stream and the consumer continues | on-call fixes, then replays from the DLQ |
| Accidental data deletion / corruption | data loss | PITR to the second before the bad transaction (Barman) | restore to a side cluster, copy back the affected rows |
| Meeting bot pod crashes | seconds of missed audio | `MeetingAttendance` workflow heartbeat. Transcript checkpointed every 5 s | relaunch from warm pool, rejoin (about 10 s) |
| Voice pipeline / GPU overloaded | slower spoken answers | GPU autoscaling, CPU fallback models | switch to meeting-chat answers |
| Employee laptop VM unhealthy | its coding task pauses | VM isolation, nightly volume snapshots | rebuild from golden image, restore volume |
| External account token revoked | that integration fails | per-connection failure, fail closed | owner asked to re-authorise. Tasks wait durably |

## 3. Resilience patterns, and where each one is used

| Pattern | Implementation |
|---|---|
| Durable execution | Temporal for runs, ingestion, schedules, connector syncs |
| Transactional outbox / inbox | every service database (see data.md §5) |
| Idempotency keys | public API (`Idempotency-Key`), `CreateTask`, tool calls (`run_id:step:call_idx`), outbox `Nats-Msg-Id` |
| Timeouts and deadline propagation | gRPC deadlines on every call (default 2 s, never infinite). Temporal `StartToClose` on every activity |
| Retries with exponential backoff and full jitter | gRPC client interceptors, with a **retry budget** (≤ 10 % extra load) to prevent retry storms. Only idempotent methods are retried |
| Circuit breakers / outlier detection | Istio for service-to-service calls. A per-connector breaker in the Integration service |
| Bulkheads | separate Temporal task queues and worker pools (`runs`, `llm`, `tools`, `sandbox`). Separate Postgres clusters by workload. Separate node pools (sandbox on gVisor nodes) |
| Back-pressure | JetStream `max_ack_pending`. Temporal worker concurrency limits. Envoy rate limits return 429 with `Retry-After` |
| Load shedding | Envoy rejects low-priority traffic (e.g. bulk API) first when the overload manager trips |
| Graceful degradation | Memory recall is optional for a run. LLM response cache. Read-only mode if pg-app is in failover |
| Health checks | `readiness` checks dependencies the pod needs to serve traffic. `liveness` checks only the process itself, so a database blip does not restart every pod in a cascade |
| Graceful shutdown | `preStop` sleep, then drain. Temporal workers stop polling and finish in-flight activities within `terminationGracePeriodSeconds` |

## 4. Capacity and scaling

- **Stateless services:** HPA on CPU and requests-per-second. Minimum 3 replicas, spread across AZs.
- **Temporal workers:** KEDA scales each task queue on backlog (`llm` and `tools` scale separately).
- **Stream consumers:** KEDA on JetStream consumer lag.
- **Postgres:** scale up vertically first, offload reads to replicas, then split databases into their own
  clusters, then add cells. Sharding inside a cell is not needed, because **cells are the shards**.
- **Load tests** (k6) in CI against a staging cell with a recorded traffic mix. A capacity model per cell (e.g.
  "one cell holds 2 000 tenants / 20 000 employees / 300 concurrent runs") determines when to open a new cell.

## 5. Proving it: chaos and drills

| Exercise | Frequency | Tool |
|---|---|---|
| Kill random pods in every namespace (staging) | continuously | Chaos Mesh |
| Postgres primary failover (staging, then production) | weekly / monthly | `kubectl cnpg promote` |
| AZ evacuation | quarterly | cordon and drain one AZ's nodes |
| Backup restore and verification | weekly, automated | CNPG restore Job plus checks |
| Region failover | twice a year | DR runbook |
| Inject LLM 429/5xx/latency | every release | fault-injection mode in the Model Gateway |
| Workflow replay against recorded histories | every PR | Temporal replayer in CI |

## 6. Observability

- **One trace per task:** a `traceparent` starts at Envoy and travels through gRPC metadata, CloudEvents
  extensions and Temporal headers, so a single Tempo trace shows API → task → workflow → each LLM and tool call.
- **LLM-specific:** OpenTelemetry GenAI semantic conventions (tokens, model, latency) in traces, plus Langfuse for
  prompt and response inspection, evaluations and regression datasets.
- **Golden signals per service** (rate, errors, latency, saturation), plus domain signals: open runs, runs
  waiting for a human, outbox lag, DLQ depth, cost per tenant per hour, approval wait time.
- **Alerting on SLO burn rate** (multi-window), not raw thresholds. Every alert links to a runbook.
