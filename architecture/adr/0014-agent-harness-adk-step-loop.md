# ADR-0014: agent-harness-adk as the agent framework, driven step by step by Temporal

**Status:** Accepted

## Context
The Agent Runtime needs an agent loop: context assembly, model calls through many providers, tool schemas,
validation and repair, guardrails, skills, prompts, memory and evaluation. Writing this from scratch duplicates a
large, well-tested body of code. [`agent-harness-adk`](https://pypi.org/project/agent-harness-adk/) (MIT, Python,
three dependencies, no vendor SDKs) already provides it.

The architecture has a hard rule ([agent-harness.md](../agent-harness.md), [ADR-0002](0002-temporal-durable-execution.md)):
**every harness stage is a Temporal activity and every iteration is rebuilt from durable state**, so a crash resumes
from the last completed step and never repeats a side effect. In version 0.2.0, `Agent.run()` drives the whole loop
inside one call, with its own sessions and approvals. Wrapping that call in a single activity would repeat completed
steps after a crash.

Three options were considered:

| Option | Trade-off |
|---|---|
| A. Add a step-level public API to the package and let Temporal drive one stage per activity | Clean fit with per-step durability. Requires a package release first |
| B. Run `Agent.run()` inside one activity | Fastest. Loses per-step durability, so a crash can repeat tool calls |
| C. Use only the package's parts and write the loop in agent-runtime | No package change. The loop is duplicated and drifts from the package |

## Decision
Option A.
- `agent-harness-adk` 0.3.0 adds a serialisable `AgentState` and public stages: `build_context`, `call_model`,
  `decide` (validation, repair loop, guardrails, loop detection), `act` (with an idempotency key per call) and a
  pure `observe`. `Agent.run()` is rebuilt on the same stages, so local behaviour does not change.
- The `EmployeeRun` workflow in Agent Runtime calls one activity per stage and keeps `AgentState` in workflow
  state. `observe` runs inside the workflow because it is pure.
- Approvals, human replies, pause, resume and cancel are **Temporal signals**. The package's own `Approvals` and
  session stores are not used inside the platform.
- Model calls use the package's `OpenAICompatibleProvider` pointed at the Model Gateway (LiteLLM), the only path to
  any LLM.
- Memory goes through a `BrainMemoryStore` that implements the package's `MemoryStore` contract over Brain `Recall`
  and `Remember`.
- Tests use `FakeProvider`, `RecordingProvider` and `ReplayProvider`. Evaluation suites use the package's
  `Evaluator`, `GoldenTask` and `Expect`.
- The platform pins a minor version range (`>=0.3,<0.4`). Any upgrade passes the Temporal replay tests before merge.

## Consequences
- One agent loop for local use and for the platform, maintained in one place.
- A crash between stages loses nothing and repeats nothing.
- A change to the stage API is a breaking change for Agent Runtime and needs a coordinated release.
- The package's own durability features (sessions, approvals) stay useful outside the platform but are deliberately
  unused inside it, so Temporal remains the single source of run state.
