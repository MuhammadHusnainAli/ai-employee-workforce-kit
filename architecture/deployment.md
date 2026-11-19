# Deployment

Diagram: page **9 · Deployment & Cells** in [diagrams/ai-workforce-architecture.drawio](diagrams/ai-workforce-architecture.drawio).

## 1. Cell-based architecture

A **cell** is a complete, independent copy of the tenant-facing stack: `core-python`, `core-rust` and the web app
with every cell module, Temporal, NATS and the Postgres clusters. Each tenant lives in exactly one cell. A thin **global control plane** (identity, tenant
directory, billing) sits above the cells.

Why cells:
- **Blast radius:** a bad deploy, a database incident or a runaway tenant affects only one cell's tenants.
- **Scaling without sharding logic:** when a cell reaches its capacity model, open a new one. Code never needs to
  know about shards.
- **Isolation as a product feature:** enterprise or regulated tenants get a **dedicated cell**, optionally in
  their preferred region (data residency). It is the same manifests with different values.
- **Safe rollouts:** deploy to the canary cell first, then roll out cell by cell.

Routing: Envoy resolves `tenant_id` (from the JWT or the API key) to a cell using a table pushed from the Tenant &
Cell Directory, cached at the edge. Webhooks from channels are routed by tenant-specific URL paths
(`/hooks/{tenant_slug}/…`).

**Start with one cell.** The cell boundary costs almost nothing on day one, but it is very expensive to add later.

### Moving a tenant between cells
A Temporal workflow handles the move: freeze new tasks for the tenant (queued at the edge), drain open runs or
move them through continue-as-new, copy the tenant's rows by logical export filtered on `tenant_id`, copy its
objects, flip the routing entry, then unfreeze. Because every row carries `tenant_id`, the copy is a filter, not
an ETL project.

## 2. Kubernetes layout (per cell)

One Kubernetes cluster per cell per region, spread across **3 availability zones**.

The backend ships as **two deployables**, `core-python` and `core-rust`, plus the web app
([ADR-0017](adr/0017-two-backend-services.md)). Every cell module runs inside the deployable of its language:

| Deployment | Namespace | Holds | Scales on |
|---|---|---|---|
| `core-python` | `app` | Workforce, Agent Runtime (live and evaluation queues), Brain, Evaluation; later Model Gateway plugins, Meeting, Realtime Voice | Temporal task-queue backlog (KEDA) and gRPC RPS |
| `core-rust` | `app` | Task, Channel, Sandbox; later Integration, Audit, Usage Metering, Workspace, Identity & Accounts | RPS and stream lag |
| `web-app` | `app` | the web app and public REST API | RPS |

Some modules launch workloads that are never part of a service process: sandbox runs (gVisor pods), meeting bots,
employee microVMs, and the GPU inference pool. When a module must scale or fail on its own (the brain API on its
latency SLO, one Temporal queue on its backlog, realtime voice on GPU locality), it is split into its own
deployment in the namespace the table below names for it. Its database, contracts and events need no change.

| Namespace | Contents | Node pool |
|---|---|---|
| `edge` | Envoy Gateway data plane (cell-local ingress) | general |
| `app` | `core-python`, `core-rust`, `web-app` | general |
| `brain` | when split out: brain-api, brain-ingest, brain-consolidate, brain-learn | general (brain-api: dedicated priority class) |
| `runtime` | when split out: Agent Runtime worker deployments, one per task queue | general (autoscaled) |
| `sandbox` | Sandbox run pods, `runtimeClassName: gvisor`, tainted nodes | sandbox (isolated) |
| `meetings` | Meeting bot pods (warm pool); Realtime Voice workers when split out | general + GPU |
| `inference` | STT / TTS / diarization / vLLM model servers | GPU (autoscaled) |
| `workspaces` | One Kata microVM per employee (`runtimeClassName: kata`) | workspace (nested-virt capable, isolated) |
| `temporal` | Temporal services + UI | general |
| `messaging` | NATS JetStream | stateful (local NVMe) |
| `data` | CloudNativePG clusters + Poolers, SeaweedFS | stateful (local NVMe) |
| `security` | OpenBao, OpenFGA, egress proxy | general |
| `observability` | OTel Collector, Prometheus agent (remote-write to central), Promtail/Alloy | general |

Rules for every workload:
- At least 3 replicas, `topologySpreadConstraints` across zones, and a `PodDisruptionBudget` (`maxUnavailable: 1`).
- Requests and limits set. Priority classes: platform > backend services > workers > batch.
- Stateful sets use local NVMe through a CSI driver that supports topology. Postgres and NATS replicate at the
  application layer, so they do not need network block storage.

## 3. Environments

| Environment | Purpose | Shape |
|---|---|---|
| `dev` | per-developer / per-PR | single node, Docker Compose or kind. 1 instance of everything, same images |
| `staging` | integration, load, chaos | one full cell, production topology at smaller size |
| `prod-canary` | first production cell: internal tenants plus opted-in customers | full cell |
| `prod-cell-NN` | customer cells | full cells |
| `dr` | warm standby region | CNPG replica clusters, scaled-down services, Argo apps ready to scale |

## 4. Delivery pipeline

```
PR → lint + unit tests + buf breaking + AsyncAPI diff + migration lint + Temporal replay tests
   → build → SBOM → Trivy scan → cosign sign → push digest
merge → Argo CD syncs staging → e2e + k6 smoke → promote (Git commit)
      → prod-canary (Argo Rollouts canary 5% → 25% → 100% with automated analysis)
      → prod cells in waves (cell-01…04, then the rest), stopped automatically if any wave's SLO burn alert fires
```
- **GitOps only:** the cluster state equals Git. No `kubectl apply` by hand in production.
- **Database migrations** run as Argo PreSync hooks and are always backward-compatible (expand/contract).
- **Feature flags** (per tenant, in the Directory) separate deploying code from releasing a feature.

## 5. Disaster recovery

| Component | DR mechanism |
|---|---|
| PostgreSQL | CNPG replica cluster in the DR region, fed from the cross-region WAL archive (RPO ≤ 5 min). Promote by setting `replica.enabled: false` |
| Temporal | its Postgres is replicated the same way. Workers resume in-flight workflows after promotion |
| NATS | the outbox makes events recoverable: unpublished rows are re-sent after failover. Streams can also mirror to the DR region |
| Object storage | SeaweedFS async replication to the DR site (or cloud S3 cross-region replication) |
| OpenBao | Raft snapshots every 15 min to DR object storage |
| Config | everything is in Git, so Argo CD rebuilds the cell from scratch |

**Runbook outline:** declare the incident → set the Directory to route the affected cells to DR → promote the
Postgres replica clusters → scale up DR workloads (Argo) → verify with synthetic tasks → announce. Rehearse twice a
year and time each step.

## 6. Local development

`just up` (Docker Compose in `deployments/`) brings up one instance each of Postgres 18 with pgvector, Temporal on
Postgres, NATS with JetStream, Valkey, SeaweedFS, LiteLLM and Grafana LGTM, on ports 7017 to 7027. `just up-all`
adds the three application containers, each after a one-shot migrate job that applies every module's migrations:

| Container | Port | Waits for |
|---|---|---|
| `core-python` | 7001 | Postgres, NATS, Temporal, Valkey, SeaweedFS, Model Gateway |
| `core-rust` | 7002 | Postgres, NATS |
| `web-app` | 7000 | both backends healthy |

They run the **same images and the same migrations** as production, so local behaviour matches production
behaviour. `just e2e` checks the main flows end to end against these containers. Keycloak, OpenFGA and OpenBao
join the local stack with the modules that need them.
