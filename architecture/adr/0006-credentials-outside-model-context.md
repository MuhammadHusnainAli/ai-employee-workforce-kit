# ADR-0006: Credentials and authorization decisions stay outside the model

**Status:** Accepted

## Context
LLMs can be manipulated through any content they read (prompt injection). If credentials or the allow/deny
decision are reachable by the model, a single malicious email can turn an AI employee against its tenant.

## Decision
- The model only proposes tool calls. The Integration Service validates each one, authorizes it with OpenFGA and a
  risk-tier policy, and requires human approval for R3 actions (and R2 to non-allowlisted targets).
- Credentials live in OpenBao and are injected only at the moment of execution. They never appear in prompts,
  Temporal history, logs or the run ledger.
- All outbound traffic from tools and the sandbox goes through an egress proxy with per-connector allowlists.

## Consequences
- A compromised model context cannot exceed the employee version's grants and policies.
- Connectors must declare tool schemas, risk tiers and idempotency classes. That is extra onboarding work per
  connector, and it is deliberate.
