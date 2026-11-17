# ADR-0016: agent-harness-adk used unchanged; the step loop lives in Agent Runtime

**Status:** Accepted (supersedes [ADR-0014](0014-agent-harness-adk-step-loop.md))

## Context
[ADR-0014](0014-agent-harness-adk-step-loop.md) planned to add a step-level API to `agent-harness-adk` itself and
release it as 0.3.0. That couples this platform's release plan to changes in a separate, general-purpose package.
The package is a published dependency like any other: this repository should consume it, not change it.

The durability rule stands: every harness stage is a Temporal activity and every iteration is rebuilt from
durable state ([agent-harness.md](../agent-harness.md), [ADR-0002](0002-temporal-durable-execution.md)).

## Decision
- `agent-harness-adk` is a normal PyPI dependency in the `pyproject.toml` of the services that need it, pinned to a
  minor range (`>=0.2,<0.3`). It is never cloned, forked or modified from this repository.
- The step loop is built in `microservices/agent-runtime/src/agent_runtime/harness/` as five stages with
  serialisable inputs and outputs, each a candidate Temporal activity:

  | Stage | Built on the package's public API |
  |---|---|
  | `build_context(spec, state)` | `ContextAssembler`, `ContextCompactor`, `close_open_tool_calls` |
  | `call_model(provider, request, state)` | `Provider.complete`, `CompletionRequest`, `estimate_cost` |
  | `decide(spec, state, response)` | `Tool.validate`, `Guardrails` |
  | `act(spec, state, calls)` | `Tool.run`, `ToolContext` (idempotency key in `ctx.state`) |
  | `observe(state, ...)` | pure, our own code: no I/O, no clock |

- `AgentState` is a pydantic model built from the package's `Message`, `ToolUseBlock` and `Usage`, so it
  round-trips through JSON and Temporal history.
- `Agent.run()` from the package is not used inside the platform. Its sessions and approvals are not used either:
  Temporal holds run state, and approvals are workflow signals.
- Tests use the package's `FakeProvider`, so they run without a network or keys.

## Consequences
- No release of the package is needed to build the platform, and the package stays general-purpose.
- The loop logic exists twice: once inside `Agent.run()` in the package, once in Agent Runtime. They can behave
  differently. Agent Runtime's version follows this repo's architecture (risk tiers, approval gate, idempotency
  keys, untrusted-content wrapping), which the package's loop does not.
- Upgrading the package to a new minor version is a deliberate change that must pass Agent Runtime's tests.
