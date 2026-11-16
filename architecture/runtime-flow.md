# Task & Agent Run Lifecycle

This is the core path of the product. Diagrams: pages **2 · Agent Run Orchestration**, **3 · Agent Harness** and **4 · Tool Call Pipeline** in
[diagrams/ai-workforce-architecture.drawio](diagrams/ai-workforce-architecture.drawio).

## Task states

```
            ┌──────────────┐
  create ──►│   QUEUED     │──(workflow started)──►┌──────────┐
            └──────────────┘                       │ RUNNING  │◄──────────────┐
                                                   └────┬─────┘               │
                     ┌──────────────────┬───────────────┼───────────────┐     │ approved /
                     ▼                  ▼               ▼               ▼     │ human replied /
              ┌────────────┐    ┌──────────────┐  ┌───────────┐  ┌──────────┐ │ resumed
              │ COMPLETED  │    │   FAILED     │  │ CANCELLED │  │ WAITING  │─┘
              └────────────┘    └──────────────┘  └───────────┘  │ _HUMAN   │
                                                                  └──────────┘
```
`WAITING_HUMAN` covers: approval required, clarification question, budget limit reached, and unknown tool outcome
(needs reconciliation). Only the Task Service writes task state. It learns about run progress from `run.*` events.

## The `EmployeeRun` workflow

Workflow ID = `task_id`. Task queue = `runs`. Every activity has an explicit timeout and retry policy.

| # | Step | Where | Failure handling |
|---|---|---|---|
| 1 | **Intake.** Task arrives from API, channel, schedule or another employee (delegation) | Task Service | `Idempotency-Key` / provider message ID → unique index, so a duplicate request returns the existing task |
| 2 | **Persist + outbox.** `INSERT task` and `INSERT outbox(task.created)` in **one transaction** | task_db | Atomic: either both rows exist or neither does |
| 3 | **Start workflow** with ID = task_id | Outbox relay → Temporal | `WorkflowIdReusePolicy=RejectDuplicate`: re-delivery is harmless |
| 4 | **Load & pin blueprint.** Fetch the employee's `active_version` and store it in workflow state | Activity → Workforce | Retried. Later edits do not affect this run |
| 5 | **Guardrail check.** Step count < `max_steps`, cost < `max_cost`, wall-clock < `max_duration`, tenant quota OK, employee not paused | Workflow code (deterministic) | Limit reached → `WAITING_HUMAN` (budget) or `FAILED` (hard limit). No silent overrun |
| 6 | **Recall.** Relevant knowledge chunks and employee memory | Activity (`llm` queue) → Brain Service (`Recall`) | Retried. If degraded, continue without memory and flag it in the step record |
| 7 | **Think.** Model call with system prompt, task, context and tool schemas | Activity (`llm` queue) → Model Gateway | Gateway falls back across providers. Activity retries 429/5xx with backoff and heartbeats while streaming. Response is stored in S3 and only a reference goes into history |
| 8 | **Decide.** Final answer → step 12. Tool call(s) → step 9 | Workflow | Malformed tool call → feed the validation error back to the model (max 2 times), then fail the step |
| 9 | **Authorize.** OpenFGA relation check plus risk-tier policy | Activity → Integration | Denied → result goes back to the model as "not permitted" and an audit event is written |
| 10 | **Approval (if the tier requires it).** Create the approval, notify the human, then `await signal("approval")` with a timer | Workflow + Task Service | Timer expiry → escalate to the manager, then `CANCELLED`. The workflow survives days of waiting with no resources held |
| 11 | **Act.** Execute the tool with `idempotency_key = run_id:step:call_idx` | Activity (`tools`/`sandbox` queue) → Integration / Sandbox | See "Unknown outcomes" below. Record the result, then loop back to **step 5** |
| 12 | **Finalize.** Deliver the result (reply in channel or webhook), write memories, emit `run.completed` | Activities | Each delivery is idempotent. Memory write is best-effort and retried, and never blocks completion |

Signals the workflow accepts at any point: `approval`, `human_reply`, `pause`, `resume`, `cancel`.
**Kill switch:** pausing an employee or tenant sends `pause` to all of its open workflows (Temporal batch
operation) and sets a flag that step 5 checks. That gives two independent stopping mechanisms.

## Unknown outcomes (the hardest case)

Suppose a worker crashes after sending "send email" to the provider but before recording the result. When the
activity retries, it reads the `tool_calls` row for its idempotency key:

| Row state | Tool supports idempotency keys? | Action |
|---|---|---|
| `succeeded` | n/a | Return the stored result. Nothing is re-executed |
| `started` (no result) | **Yes** (e.g. Stripe, many REST APIs) | Re-send with the same provider idempotency key |
| `started` (no result) | **No, but it can be checked** | Run the connector's `reconcile()` (e.g. search the Sent folder for the `X-Run-Step` header) |
| `started` (no result) | **No, and it cannot be checked** | Mark `UNKNOWN` → task `WAITING_HUMAN`: "Did this action happen?" **Never retry blindly** |

Each connector declares its idempotency class in its MCP tool metadata, and the classification is reviewed when
the connector is onboarded.

## Keeping workflows healthy

- **History size:** use `continue-as-new` every 100 steps or when history exceeds 10 MB. Payloads stay in S3, so
  history holds only references.
- **Determinism:** workflow code makes no I/O, no clock calls and no random calls. All of that happens in
  activities. A replay test runs in CI against recorded histories.
- **Deploying new workflow code:** Temporal Worker Versioning (or `patched()` markers). In-flight runs finish on
  the version they started with.
- **Payload encryption:** a Temporal payload codec encrypts with a per-tenant data key from OpenBao, so Temporal's
  database holds only ciphertext.

## Employees delegating to employees

A manager employee can create sub-tasks for other employees through a tool (`delegate_task`), which calls
`Task.CreateTask` with `parent_task_id`. Each sub-task is its own workflow. The parent waits on a `child_completed`
signal. These are not Temporal child workflows: keeping them as separate workflows lets each one have its own
guardrails, audit trail and retry scope. Depth is capped at 3 and fan-out at 10 per step, which prevents runaway
recursion.
