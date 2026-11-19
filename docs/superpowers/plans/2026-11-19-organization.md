# AI Organisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Org chart with a co-CEO under the person, approved hiring, delegation down the chart with results
flowing up, employee messages and calls, and co-CEO standups the person can join, all visible in the web app.

**Architecture:** Approach A of the spec: `workforce` (org chart, hiring), `task` in core-rust (sub-tasks),
`agent_runtime` (org tools, waiting for reports), a new `collaboration` module in core-python (conversations,
calls, standup workflow), and web app pages over REST.

**Tech Stack:** Python 3.13 (grpc.aio, psycopg, Temporal, agent-harness-adk), Rust 2024 (tonic, sqlx), Next.js.

**Spec:** `docs/superpowers/specs/2026-11-19-organization-design.md`

## Global Constraints

- Caps: delegation depth 3; 20 children per task; 10 delegations per step; 6 turns per call; 10 next steps per
  standup; child output cut to 4,000 characters; call answers under 150 words; standup updates in batches of 5;
  discussion window default 120 s.
- One co-CEO per tenant. Only a person approves a hire.
- Every new tenant table: `tenant_id`, RLS enabled and forced, `tenant_isolation` policy. Events through the outbox
  in the same transaction.
- New workflow behaviour behind `workflow.patched("org-delegation-v1")`.
- No new ports. No em dashes. One task, one commit, no Co-Authored-By trailer.

## Review Focus

1. Delegating to someone not below you, to yourself, or to a paused employee: the model gets an error result,
   the run does not fail. (Task 5 tests.)
2. A retried `act` activity repeats `delegate_task`: still one sub-task (idempotency key). (Task 5 test.)
3. A child that fails or is cancelled still wakes its parent with that status. (Tasks 3 and 6 tests.)
4. Cancelling a goal cancels every unfinished sub-task below it. (Task 3 test.)
5. A person posts in a finished standup or a closed call: `FAILED_PRECONDITION`, nothing stored. (Task 4 test.)

---

### Task 1: Org chart in workforce

**Files:** Create `core-python/migrations/workforce/0003_organization.{up,down}.sql`,
`src/core_python/workforce/domain/org.py`, `src/core_python/workforce/adapters/postgres/org.py`,
`tests/workforce/test_org_domain.py`, `tests/workforce/test_org_integration.py`. Modify
`contracts/proto/wfk/workforce_service/v1/workforce.proto`, `src/core_python/workforce/api/workforce.py`,
`src/core_python/workforce/app.py`.

**Interfaces (produces):**
- `domain.org`: `Member(employee_id, name, title, department_id, manager_id, is_co_ceo, status)`;
  `check_placement(members: dict[str, Member], who: str, manager_id: str | None, is_co_ceo: bool) -> None`
  (raises `InvalidPlacement`); `below(members, manager_id) -> set[str]`;
  `can_delegate(members, from_id, to_id) -> bool` (co-CEO: anyone active except itself; others: `below`, active).
- RPCs `CreateDepartment`, `ListDepartments`, `PlaceEmployee(employee_id, title, department_id, manager_id,
  is_co_ceo)`, `GetOrgChart -> (members, departments)`. Events `employee.department_created`, `employee.placed`.

- [ ] Tests: cycle rejected, second co-CEO rejected, co-CEO with a manager rejected, `below` transitive,
  `can_delegate` rules; integration: place, chart, other tenant invisible.
- [ ] Implement, `just gen`, `just ci` in core-python.
- [ ] Commit `Add departments and reporting lines to workforce`.

### Task 2: Hiring with the person's approval

**Files:** Create `migrations/workforce/0004_hire_requests.{up,down}.sql`, `adapters/postgres/hiring.py`,
`domain/hiring.py`, tests. Modify the proto and API.

**Interfaces (produces):** `default_grants(position: Literal["co_ceo","manager","member"]) -> list[ToolGrant]`
(spec table); RPCs `HireEmployee`, `RequestHire`, `ListHireRequests(status)`, `DecideHireRequest(request_id,
approved, note)`. Approving creates employee, version and placement in one transaction; deciding twice is
`FAILED_PRECONDITION`; `RequestHire` idempotent on its key. Events `employee.hire_requested`,
`employee.hire_decided`.

- [ ] Tests: approve creates a placed employee with default grants; reject creates nothing; decide twice fails;
  a request by an employee never creates an employee by itself.
- [ ] Implement, `just ci`. Commit `Add hire requests approved by a person`.

### Task 3: Sub-tasks in the Rust task module

**Files:** Create `core-rust/migrations/task/0003_delegation.{up,down}.sql`. Modify `tasks.proto`,
`src/task/{domain,adapters,api}.rs`, `tests/tasks.rs`.

**Interfaces (produces):** `CreateTaskRequest.parent_task_id`, `Task.{parent_task_id, root_task_id, depth,
output, delegated_by}`, `ListTasksRequest.{parent_task_id, root_task_id}`; event `task.child_finished` with
`{task_id: parent, child_task_id, employee_id, status, stop_reason, output}`; `task.created` carries
`parent_task_id`. Domain consts `MAX_DEPTH = 3`, `MAX_CHILDREN = 20`.

- [ ] Tests: depth 4 refused; 21st child refused; child under a finished parent refused; finishing a child emits
  one `task.child_finished` (also for failed and cancelled); cancel cascades to descendants; output stored.
- [ ] Implement, `cargo test -- --include-ignored`, clippy. Commit `Add sub-tasks with depth and fan-out caps`.

### Task 4: Collaboration module: conversations and calls

**Files:** Create `core-python/migrations/collaboration/0001_platform_outbox_inbox.*`, `0002_conversations.*`,
`contracts/proto/wfk/collaboration_service/v1/collaboration.proto`, `src/core_python/collaboration/`
(`settings.py`, `app.py`, `domain/conversations.py`, `adapters/postgres/conversations.py`,
`adapters/answerer.py`, `api/collaboration.py`), `docs/modules/collaboration.md`, tests. Modify
`src/core_python/{settings,app,main}.py` (collab database, service, outbox relay to `COLLAB`).

**Interfaces (produces):** RPCs `SendMessage`, `PostMessage`, `ListConversations`, `GetConversation(after_seq)`,
`ReadInbox`, `AskColleague`. `Answerer.answer(callee_spec: CalleeSpec, transcript: list[Msg], question) -> str`
over an agent-harness `Provider` (tests use `FakeProvider`). Callee spec from `WorkforceService.GetVersion`.

- [ ] Tests: one direct conversation per pair; inbox marks read; call caps at 6 turns then closed; posting to a
  closed conversation is `FAILED_PRECONDITION`; other tenant invisible; idempotent post.
- [ ] Implement, `just ci`. Commit `Add collaboration module with messages and calls`.

### Task 5: Org tools for employees

**Files:** Create `src/core_python/agent_runtime/org_tools.py`, `adapters/org_clients.py`, tests. Modify
`tool_catalog.py` (`build_catalog(clients: OrgClients | None) -> dict[str, Callable[[], Tool]]`),
`main.py`, `settings.py` (`task_address`, `collab_address`).

**Interfaces (produces):** tools `list_team`, `delegate_task(employee_id, title, instructions)`,
`wait_for_reports()`, `send_message(to, text)`, `read_messages()`, `ask_colleague(employee_id, question,
call_id="")`, `request_hire(employee_id, name, title, department_id, role, reason)`, `start_standup(department_id="")`.
Each reads tenant, employee and idempotency key from `ToolContext.state`, returns a short text result, and turns
gRPC errors into a readable message (never raises).

- [ ] Tests (fake clients): delegate outside the chart refused; more than 10 delegations in one step refused;
  idempotency key reaches `CreateTask`; errors become text.
- [ ] Implement, `just ci`. Commit `Add org tools for delegation, messages and calls`.

### Task 6: Waiting for reports in EmployeeRun

**Files:** Modify `workflows/employee_run.py`, `workflows/types.py` (`ChildResult`), `adapters/tasks.py`
(`task.child_finished` -> signal `child_finished`), `harness/observe.py` (`reports_back(state, text, ts)`),
tests in `tests/agent_runtime/`.

- [ ] Tests (Temporal test env, `FakeProvider`): wait returns all children's results; final with open children
  waits and continues; failed child reported as failed; recorded replay histories still pass.
- [ ] Implement, `just ci`. Commit `Wait for delegated reports in employee runs`.

### Task 7: Standups

**Files:** Create `collaboration/standup/{workflow,activities,facts}.py`, `collaboration/worker.py`,
`migrations/collaboration/0003_standups.*`, tests. Modify proto (`StartStandup`, `GetStandup`, `ListStandups`,
`EndStandupDiscussion`), API, `core_python/main.py` (worker part `collaboration`).

- [ ] Tests: idle employee gets the fixed line with no model call; person's post gets a co-CEO reply; window
  closes on end signal; summary parsed into next steps (at most 10); `assign_next_steps` creates one task per
  step idempotently.
- [ ] Implement, `just ci`. Commit `Add co-CEO standups the person can join`.

### Task 8: Web app

**Files:** Copy new protos into `web-app/contracts/`; create `lib/api/{org,collaboration}.ts`, routes under
`app/api/v1/{org,conversations,standups}`, pages `app/org`, `app/conversations`, `app/conversations/[id]`,
`app/standups`, `app/standups/[id]`; modify `app/tasks/[id]/page.tsx` (sub-tasks), nav, OpenAPI, tests.

- [ ] `npm run typecheck`, `npm test`. Commit `Add organisation, conversations and standup pages`.

### Task 9: Stack, end to end and docs

Compose env (`WFK_COLLAB_DATABASE_URL`, `WFK_TASK_ADDRESS`), `deployments/e2e/organization.sh` (hire through a
request and approval, delegation tree via the API, a call, a standup with a person's post), module docs,
CLAUDE.md layout line, plan.md outcome.

- [ ] `just up-all`, `just e2e`. Commit `Run the organisation end to end on the stack`.
