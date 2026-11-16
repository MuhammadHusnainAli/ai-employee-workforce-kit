# Agent Harness

The harness is everything around the model: what goes into the prompt, how the model is called, how its output is
checked, how actions are carried out, and when the loop stops. The model only **proposes**. The harness
**decides**, deterministically, and records every step.

Diagrams: **3 · Agent Harness** (one iteration in depth), **2 · Agent Run Orchestration** (how iterations are
driven durably) and **4 · Tool Call Pipeline** in
[diagrams/ai-workforce-architecture.drawio](diagrams/ai-workforce-architecture.drawio).

## Design rules

1. **Every iteration is rebuilt from durable state.** Nothing is carried in process memory between iterations. A
   crash between two iterations loses nothing, and the next worker builds exactly the same context.
2. **Each stage is a Temporal activity with its own timeout and retry policy** (see the activity contract table on
   page 2), so one failing stage never restarts the whole run.
3. **Deterministic checks first, model checks second.** Schemas, allowlists, budgets and regex scanners run before
   any LLM-based classifier.
4. **Everything is bounded:** context size, repair attempts, steps, cost, wall-clock time and parallel fan-out.
5. **Everything is recorded:** prompt hash and template version, model, tokens, cost, the decision and the reason,
   per step, in `run_steps` and in the trace.

## Stage 1: Context engine

| Part | What it does | Reliability detail |
|---|---|---|
| Pinned inputs | Task and thread, the employee **version** (role, instructions, policies), tool grants, knowledge bindings, model policy, the current plan and running summary | Version pinned at run start. Edits mid-run never change behaviour |
| Retrieval | Rewrites the query from the task and plan step, runs hybrid search (pgvector HNSW alongside Postgres FTS), fuses with RRF, applies a cross-encoder rerank and takes the top-k with citations | Tenant RLS on every query. Degrades to "no retrieval" (flagged in the step) instead of failing the run |
| Brain recall | `Brain.Recall` returns ranked, cited items from episodic, semantic-graph, skill and rule memory, plus the pinned identity core ([brain.md](brain.md)) | Memory is per employee and per tenant. Secrets are never stored |
| Tool manifest | Only the tools in the version's grants, with JSON Schemas and risk tiers. With more than 30 tools, the relevant subset is retrieved | The model cannot see, and therefore cannot call, ungranted tools |
| Token budgeter | Splits the window: system 10 %, tools 15 %, task 10 %, memory 10 %, knowledge 25 %, history 25 %, output reserve 5 % | Context can never overflow. Lower-priority sections are trimmed first |
| Compaction | When history exceeds 70 % of its share, the oldest turns are summarised and the last N are kept verbatim. Large tool outputs go to S3 with a summary in the prompt (claim-check) | The summary is stored in workflow state, so compaction is replay-safe |
| Prompt assembly | Stable prefix (system prompt and tools), then a cache breakpoint, then dynamic content. Untrusted content is wrapped in `<data source=… trust=untrusted>` blocks | The prompt hash and template version are recorded, so any step can be reproduced |

## Stage 2: Reason and call the model

- **Plan-and-execute.** The first iteration produces an explicit step plan. Later iterations revise it from
  observations. The plan lives in workflow state: small and durable.
- **Model routing.** Planning and hard reasoning go to a frontier model. Extraction and classification go to a
  small, fast model. Each employee's model policy and the tenant's data-residency rules constrain the choice.
- **Budget pre-check.** Estimated tokens are reserved against the run, employee and tenant budgets before the call.
  If the budget is insufficient, the run moves to `WAITING_HUMAN` instead of overspending.
- **Model Gateway call.** LiteLLM with streaming and native tool use. 180 s timeout, heartbeat every 15 s, retries
  on 429/5xx with backoff and jitter, and a fallback chain across providers and self-hosted vLLM. The response is
  stored in S3 and only a reference goes into workflow history.
- **Metering and caching.** Tokens, cost and latency go into a `usage.llm` event and an OTel GenAI span. Provider
  prompt caching applies to the stable prefix. The response cache is used only for temperature-0 calls and is keyed
  per tenant.

## Stage 3: Parse, validate, decide

| Check | Rule | On failure |
|---|---|---|
| Structured output | Actions arrive as native tool-use blocks. Final answers are checked against a response schema | Repair loop |
| Schema validation | JSON Schema per tool (types, enums, ranges). Referenced IDs must exist for the tenant. Unknown tool names are rejected | Repair loop |
| Repair loop | The validation error is fed back to the model, at most twice per step | Non-retryable step failure |
| Output guardrails | Secret scanner, tenant PII policy (redact or block), grounding (claims must cite retrieved chunks), policy classifier on outbound text | Block, then ask to revise or escalate |
| Progress and loop detector | The same tool with the same argument hash 3 times, no plan progress in 5 iterations, or oscillation between two states | Stop, then `WAITING_HUMAN` |

The result is exactly one of: **final answer**, **tool calls**, **ask a human**, **give up**.

## Stage 4: Act (tool router)

1. **Classify** each call by risk tier (R0 read-internal, R1 write-internal, R2 external communication,
   R3 irreversible or financial). Reads run in parallel (up to 5). Writes run serially, in the order the model gave.
2. **Authorize**: schema check, OpenFGA relation check, tier policy, then constraints (recipient allowlists, daily
   caps, size limits).
3. **Approval gate** for R3, and for R2 to targets not on the allowlist. The human sees the exact arguments and may
   edit them. The wait is durable, with escalation and expiry timers.
4. **Execute** with the idempotency key `run_id:step:call_idx`. The credential is injected from OpenBao at this
   point only. The call goes through the MCP connector and then the egress proxy, with a per-connector circuit
   breaker. The result and the outbox event are written in one transaction.
5. **Workspace or sandbox**: shell, file and browser actions run on the employee's own laptop (see
   [digital-employee.md](digital-employee.md)). Untrusted code runs in an ephemeral gVisor sandbox.
6. **Normalize**: large outputs are truncated or summarised, the error class is attached (retryable,
   non-retryable or unknown), and the result is wrapped as an untrusted observation with its provenance.

The unknown-outcome rules (provider key resend, `reconcile()`, or ask a human) are in
[runtime-flow.md](runtime-flow.md#unknown-outcomes-the-hardest-case) and on diagram page 4.

## Stage 5: Observe, learn, stop

- **Append the observation.** The `run_steps` row and the outbox event are written in one transaction, then
  `run.step_completed` goes out.
- **Stop conditions** are checked every loop:
  - the final answer passed all guardrails;
  - `max_steps`, `max_cost` or `max_duration` was reached;
  - a loop was detected;
  - the run was paused or cancelled;
  - an unrecoverable error occurred.

  If none applies, the next iteration starts.
- **Self-check (high-risk only).** A critic pass reviews final answers that trigger R2/R3 delivery. If it fails,
  the answer is revised once or escalated to a human.
- **Memory write (end of run).** Facts, preferences and outcomes are extracted, deduplicated by embedding
  similarity, scored for salience and given a TTL. This is a best-effort activity that never blocks completion.
- **Quality loop.**
  - A Langfuse trace is kept per run.
  - An LLM judge scores a 5 % online sample.
  - Thumbs up/down and corrections from humans feed evaluation datasets.
  - **An offline eval suite must pass before a new employee version can be published.**

## Harness modes

The same harness runs in three modes. They differ only in their latency budget and in which tools they can use:

| Mode | Used for | Model call | Tools |
|---|---|---|---|
| **Task** | Normal background work | Frontier model, full guardrails | All granted tools |
| **Conversation** | Chat and email threads | Same, with a shorter context and a reply-time target | All granted tools |
| **Live meeting** | Answering by voice in a meeting | Fast model, first token under 400 ms, streaming TTS | Read-only (R0) during the call. Anything else becomes a task after the meeting ("I'll follow up") |
