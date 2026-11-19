# The AI organisation: org chart, delegation, conversations and standups

Status: approved (approach A), 2026-11-19.

## Intent

The person who owns the workspace runs a company of AI employees. They are the CEO. An AI **co-CEO** sits
directly under them and runs the company day to day. Employees are hired into **departments** (Sales,
Marketing, ...) with a **title** (Head of Sales, Junior Sales, Sales Intern) and a **manager**. The person gives a
goal to the co-CEO only; the co-CEO splits it and hands pieces down the chart, and each level hands smaller pieces
further down until the work is done end to end and the results flow back up. Employees **message** each other and
hold short **calls** to ask each other things. The co-CEO runs **standups** (where we stand, where we need to go)
that the person can join. The person can read every conversation.

## Goals

1. Departments, titles and reporting lines, with exactly one co-CEO per workspace.
2. Hiring: the person hires directly; an employee (normally the co-CEO) can only *request* a hire, which waits
   for the person's approval, because a hire costs money.
3. Delegation down the chart: a manager gives a task to anyone below them; the sub-task runs as that employee's
   own durable run; the manager waits for the results and continues with them.
4. Employee to employee messages (asynchronous) and calls (a short question and answer exchange).
5. Standups run by the co-CEO from real task status, joinable by the person, ending in "where we are", "where we
   go" and owned next steps.
6. Everything visible in the web app: org chart, hire requests, goals and sub-task trees, every conversation,
   standup room.
7. Efficient by construction (see "Keeping it efficient").

## Non-goals

- Real outside tools for Sales and Marketing (email, CRM): they come with the Integration Service (plan 4.3).
- Voice calls. A "call" here is text; voice belongs to Realtime Voice (Phase 9).
- Payroll or billing for employees. "Costs money" is enforced through approvals, not accounting.

## Anti-goals (a failure even if it works)

- An employee spends money or hires without the person's approval.
- Delegation or calls that loop or fan out without limit.
- Conversations the person cannot see.
- One module reading another module's tables.

## Constraints

From CLAUDE.md and the ADRs: capabilities are modules inside `core-python` or `core-rust`; each module owns its
database, migrations and proto contract; every tenant table has forced RLS; state changes write their event to
the outbox in the same transaction; all Temporal workflows are Python; `agent-harness-adk` is used, never
changed; no new ports; no em dashes.

## Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| A | Spread across the existing modules: org chart and hiring in `workforce`, sub-tasks in `task` (Rust), a new `collaboration` module for messages, calls and standups, web pages | Follows the module rules; each part testable alone; more files to touch | **Chosen** by the person |
| B | One new `organization` module holding everything | Faster first cut, but duplicates the task inbox and owns data that belongs to `task` and `workforce` | Rejected |
| C | The co-CEO simulates its team inside one run | Cheapest, but no real employees, memory, audit trail or visible conversations | Rejected: works against the goal |

## Decided by the person

- The person is the CEO; the AI is the co-CEO. Only the person approves anything that costs money.
- The co-CEO runs standups; the person can join.
- Employees can message each other and arrange calls; the person sees every conversation.
- Otherwise employees act on their own.

## Design

### 1. Org chart and hiring (`workforce`, Python)

Migration `0003_organization`:

- `departments (tenant_id, department_id, name, head_employee_id)`.
- `employees` gains `title`, `department_id`, `manager_id`, `is_co_ceo` (a partial unique index allows one
  co-CEO per tenant).
- `hire_requests (request_id, requested_by, requested_by_kind, employee_id, name, title, department_id,
  manager_id, role, instructions, model, reason, max_cost_usd, status pending|approved|rejected, decided_by,
  decision_note, idempotency_key, ...)`.

Rules (pure, in `domain/org.py`): a manager must exist and be active; the reporting line has no cycles; the
co-CEO has no manager; anyone without a manager reports to the person. **Who can delegate to whom:** the co-CEO
to anyone; anyone else to the employees below them in the chart.

New RPCs on `WorkforceService`: `CreateDepartment`, `ListDepartments`, `PlaceEmployee`, `GetOrgChart`,
`HireEmployee` (the person: create, publish and place in one transaction), `RequestHire` (an employee),
`ListHireRequests`, `DecideHireRequest` (the person; approving runs the same transaction as `HireEmployee`).
Events: `employee.placed`, `employee.department_created`, `employee.hire_requested`, `employee.hire_decided`.

**Default tool grants by position** (written into the version at hire time):

| Position | Tools |
|---|---|
| everyone | `list_team` (R0), `read_messages` (R0), `send_message` (R1), `ask_colleague` (R1) |
| has or may have reports (co-CEO, department heads) | plus `delegate_task` (R1), `wait_for_reports` (R0) |
| co-CEO | plus `request_hire` (R1), `start_standup` (R1) |

### 2. Delegation (`task` in Rust, `agent_runtime` in Python)

Migration `0003_delegation` on `task_db`: `parent_task_id`, `root_task_id`, `depth`, `output`, `delegated_by`.

- `CreateTask` takes an optional `parent_task_id`. The parent must exist and be unfinished; depth is the
  parent's plus one and **at most 3**; a parent has **at most 20 children**.
- The run's final output is stored on the task when `run.completed` / `run.failed` arrive.
- When a child finishes, the same transaction emits `task.child_finished` addressed to the parent
  (`task_id` = parent, plus child id, employee, status, output cut to 4,000 characters).
- Cancelling a task cancels its unfinished descendants.
- `ListTasks` filters by `parent_task_id` and `root_task_id`; `Task` carries the new fields.

In `agent_runtime`:

- Tools that need other modules get their clients from an `OrgClients` object; the catalog becomes a factory
  that builds the in-process tools plus the org tools.
- `delegate_task(employee_id, title, instructions)` checks the chart (`GetOrgChart` + the domain rule),
  then calls `CreateTask` with `parent_task_id` = this run and idempotency key = the call's key. At most
  **10 delegations per step**.
- `EmployeeRun` gets a `child_finished` signal (agent-runtime's task inbox turns `task.child_finished` into it).
  A `wait_for_reports` call is handled by the workflow itself: it waits durably until every child this run
  delegated has finished (or the run's wait timeout passes), then returns all their results as that call's
  result. If the model gives a final answer while children are still open, the workflow waits for them the same
  way and hands their results back to the model instead of finishing, so results always flow up.
- New code paths are behind `workflow.patched("org-delegation-v1")` so recorded histories replay unchanged.

Spending: runs already stop on their cost limit and ask a person; R3 tools ("irreversible or financial") always
ask a person. Together with hire requests, that is "anything that costs money asks the person".

### 3. Conversations and standups (`collaboration`, new Python module)

Database `collab_db`, migrations in `migrations/collaboration/`, contract `wfk.collaboration_service.v1`.

Tables: `conversations (conversation_id, kind direct|call|standup, topic, status, started_by, run_id, turns,
max_turns, summary)`, `participants (conversation_id, participant, kind employee|person, last_read_seq)`,
`messages (conversation_id, seq, author, author_kind, kind, text, idempotency_key)`, `standups (standup_id,
scope, led_by, status collecting|discussing|summarising|done|failed, reports, summary, next_steps)`. Every table
tenant-scoped with forced RLS; events `collab.message_posted`, `collab.standup_started`,
`collab.standup_finished` on stream `COLLAB`.

RPCs: `SendMessage` (one direct conversation per pair, reused), `PostMessage` (a person joins any conversation),
`ListConversations`, `GetConversation` (messages after a sequence, for live polling), `ReadInbox` (unread for
an employee, marks them read), `AskColleague`, `StartStandup`, `GetStandup`, `ListStandups`,
`EndStandupDiscussion`.

**Calls.** `AskColleague(caller, callee, question, conversation_id?)` records the question, answers it as the
callee (one model call with the callee's role and instructions and the conversation so far, no tools, under 150
words), records the answer and returns it. The caller's own step loop drives the back and forth by asking
again with the same `conversation_id`. A call is capped at **6 turns**, then closes.

**Standups** are a Temporal workflow `Standup` on queue `collab`:

1. Roster from `GetOrgChart` (whole company or one department), ordered by the chart.
2. Facts per employee from `ListTasks` (core-rust): finished since the last standup, in progress, waiting on a
   person, failed.
3. One update per employee (done, next, blockers). An employee with no activity gets a fixed line with **no
   model call**; the others get one short model call each, in parallel batches of 5.
4. Discussion: the person can post; each post gets a reply from the co-CEO. The window closes after
   `discussion_s` with no new post (default 120 s) or when the person ends it. Posting signals the workflow; the
   workflow always re-reads messages from the database, so a lost signal only delays a reply.
5. The co-CEO writes the summary: where we are, where we go, next steps (owner and action), risks. With
   `assign_next_steps`, each step becomes a task for its owner (idempotent per standup and step).

Anyone can start one: the person from the web app, the co-CEO through `start_standup`.

### 4. Web app

Pages: **Organisation** (chart by department, give the co-CEO a goal, hire, create department, pending hire
requests with approve or reject), **Conversations** (every direct conversation, call and standup, with live
messages and a box for the person to post), **Standups** (start one, live room, summary and next steps), and
the task page shows the sub-task tree. REST routes under `/api/v1/org`, `/api/v1/conversations`,
`/api/v1/standups`, added to the OpenAPI document.

### Keeping it efficient

- Hard caps: depth 3, 20 children per task, 10 delegations per step, 6 turns per call, 10 next steps.
- Standups read task status instead of asking everyone to write essays; idle employees cost no model call;
  updates run in parallel batches.
- Calls answer in one model call per turn with no tools and a 150 word cap.
- Child results are cut to 4,000 characters before they go up.
- Org chart and version lookups are cached where they cannot change (versions) and read once per call otherwise.
- Pages poll a sequence number, so a refresh fetches only new messages.

## Left to the builder (calls I made)

- Default tool grants by position (table above).
- Messages are asynchronous: a recipient reads them with `read_messages`; a message does not start a run.
- Calls answer from the callee's role and instructions only (no brain recall), to keep them cheap.
- `assign_next_steps` is off by default.
- No scheduled daily standup in this round; Temporal Schedules can start `Standup` later.
