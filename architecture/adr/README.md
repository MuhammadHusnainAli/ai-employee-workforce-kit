# Architecture Decision Records

Each ADR records one significant decision: its context, the decision, and its consequences. ADRs are never edited
after acceptance. A changed decision gets a new ADR that supersedes the old one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-service-boundaries.md) | Cut services by business capability (16 cell services, not nano-services) | Boundaries accepted; deployables superseded by 0017 |
| [0002](0002-temporal-durable-execution.md) | Temporal for durable agent execution | Accepted |
| [0003](0003-postgresql-system-of-record.md) | PostgreSQL (CloudNativePG) as the only system of record | Accepted |
| [0004](0004-outbox-and-nats-jetstream.md) | Transactional outbox + NATS JetStream for events | Accepted |
| [0005](0005-tenancy-rls-and-cells.md) | Shared-schema tenancy with RLS; cells for isolation and scale | Accepted |
| [0006](0006-credentials-outside-model-context.md) | Credentials and authorization decisions stay outside the model | Accepted |
| [0007](0007-open-source-license-policy.md) | OSI-approved licenses only | Accepted |
| [0008](0008-realtime-voice-pipeline.md) | Self-hosted streaming voice pipeline (Pipecat) behind platform adapters | Accepted |
| [0009](0009-per-employee-workspace-microvm.md) | One persistent microVM workspace ("own laptop") per employee | Accepted |
| [0010](0010-ai-employees-are-disclosed.md) | AI employees work like colleagues but are always disclosed as AI | Accepted |
| [0011](0011-brain-service.md) | A dedicated Brain Service with bi-temporal memory in PostgreSQL | Accepted |
| [0012](0012-gated-brain-evolution.md) | Brain evolution through versioned, evaluated, reversible proposals | Accepted |
| [0013](0013-rust-and-python.md) | Rust and Python as the two implementation languages (supersedes Go) | Accepted |
| [0014](0014-agent-harness-adk-step-loop.md) | agent-harness-adk as the agent framework, driven step by step by Temporal | Superseded by 0016 |
| [0015](0015-self-contained-microservices.md) | Every microservice is a self-contained project: own manifest, lockfile, platform code and contracts | Accepted; applies to the two services of 0017 |
| [0016](0016-harness-package-as-dependency.md) | agent-harness-adk used unchanged as a dependency; the step loop lives in Agent Runtime | Accepted |
| [0017](0017-two-backend-services.md) | Two backend services, one per language: core-python and core-rust, each former service a module | Accepted |
| [0018](0018-remove-service-templates.md) | Remove the service templates and `just new-service`; new capabilities are modules | Accepted |
