# ADR-0013: Rust and Python as the two implementation languages

**Status:** Accepted (supersedes the Go choice in the first draft of [README.md](../README.md#3-technology-choices))

## Context
The first draft used three languages: Go for transactional services, Python for the agent runtime and brain, and
TypeScript for the web app. Every extra backend language doubles the platform libraries (outbox, inbox, tenancy,
telemetry), the service templates and the CI pipelines. The platform has two kinds of backend work:

- **Hot, high-volume, latency-sensitive paths:** inbound channels, the task inbox, the tool call pipeline, audit,
  usage metering, sandbox and workspace lifecycle, recall ranking.
- **LLM-facing and orchestration work:** Temporal workflows, the agent harness, extraction, evaluation, voice.

Temporal has production-ready SDKs for Python but not for Rust. The agent framework we adopt
([ADR-0014](0014-agent-harness-adk-step-loop.md)) is a Python package.

## Decision
- Backend services are written in **Rust** or **Python**. Go is not used.
- **Rust** for throughput-heavy and latency-critical services: Channel Gateway, Task Service, Integration & Tool
  Service, Sandbox Runner, Audit Service, Usage Metering, Workspace Manager, Identity & Accounts.
- **Python** for Agent Runtime, Brain Service, Model Gateway, Meeting Service, Realtime Voice, Evaluation Service,
  Workforce Service, Tenant & Cell Directory and Billing.
- **All Temporal workflows and activity workers are Python.** Rust services are called from Python activities over
  gRPC.
- Rust code that Python needs in-process (for example Brain recall fusion, ranking and packing) ships as a **PyO3**
  module built with **maturin**.
- The web app stays **TypeScript (Next.js)**.
- Contracts stay language-neutral: Protobuf for gRPC, CloudEvents with AsyncAPI for events.

## Consequences
- Two sets of platform libraries (`libs/python`, `libs/rust`), two service templates, two CI tracks.
- Rust services get predictable latency and low memory per pod. Python keeps the LLM and Temporal ecosystems.
- A Rust service can never own a workflow. If Temporal releases a stable Rust SDK, a new ADR can revisit this.
- Hot paths inside Python services can move to Rust without a new network hop, through PyO3.
