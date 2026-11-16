# ADR-0002: Temporal for durable agent execution

**Status:** Accepted

## Context
An agent run is a long, multi-step, failure-prone process. It makes LLM calls lasting seconds to minutes, calls
third-party APIs, and may wait days for a human approval. It must survive pod restarts, deploys and dependency
outages without repeating or skipping steps.

Alternatives considered: (a) a home-made state machine on a queue plus a status table, (b) Celery/BullMQ-style
job queues, (c) Argo Workflows (built for batch DAGs, not interactive signals).

## Decision
Every run is a Temporal workflow (`EmployeeRun`, workflow ID = task ID). LLM, tool, memory and delivery calls are
activities on separate task queues. Temporal persists to its own PostgreSQL cluster (`pg-temporal`).

## Consequences
- Retries, timeouts, timers, signals (approve, pause, cancel), resumption and an execution history come built in.
- Workflow code must be deterministic. Replay tests in CI enforce this.
- History limits require the claim-check pattern for large payloads and continue-as-new for long runs.
- Temporal becomes critical infrastructure, so it gets an isolated database, 3-AZ deployment and dedicated
  monitoring.
