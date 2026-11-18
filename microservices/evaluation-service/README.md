# Evaluation Service

| | |
|---|---|
| **Scope** | cell |
| **Language** | Python |
| **Database** | `eval_db` on `pg-brain` |
| **Local port** | none: a Temporal worker (queue `eval`); the 7000-7020 range is full |

## Purpose
Release gate for employee behaviour (brain.md section 6, step 3): every proposed brain change is run through
golden, regression and safety suites, with and without the change, before anyone may approve it.

## How an evaluation runs
1. `brain.change_proposed` (BRAIN stream) starts `EvalProposal` (workflow id `eval-<proposal>`: once per
   proposal).
2. **Suite:** golden tasks for the employee (`suites/golden/<employee>.yaml`, else `default.yaml`), every safety
   task (`suites/safety/`), and a regression set from the employee's past successes (from brain-service).
3. **Baseline and candidate:** the harness `Evaluator` runs the suite twice through real EmployeeRuns on
   agent-runtime's evaluation queue (`eval-runs`, served by `agent-runtime worker --prefix eval- --no-loops`), in
   mode `eval`; the second time with the proposal as a candidate brain change. Eval runs are marked: nothing learns
   from them and nobody is answered with them.
4. **Verdict:** passed only if nothing regressed, every safety task passed, and the score did not fall. Stored once
   in `eval_runs` with both reports, then `eval.completed` (and `eval.regression_detected`).

No report means no promotion: brain-service treats a missing verdict as a failure.

## Events
| Direction | Stream | Subjects |
|---|---|---|
| In | `BRAIN` (consumer `evaluation-proposals`) | `brain.change_proposed` |
| Out | `EVAL` | `eval.completed` · `eval.regression_detected` |

## Depends on
- Temporal (its own queue `eval`; agent-runtime's `eval-runs`)
- brain-service (`ListMemories`, for regression sets; `WFK_BRAIN_ADDRESS`)

## Folder layout
```
evaluation-service/
  suites/golden, suites/safety       the suites (YAML; agent-harness-adk's GoldenTask and Expect)
  src/evaluation_service/domain/     suites, regression sets, the verdict (no I/O)
  src/evaluation_service/adapters/   the employee as an Evaluator agent, eval_db, brain-service
  src/evaluation_service/workflows.py, activities.py, main.py
  migrations/                         0001 outbox/inbox, 0002 eval_runs
  tests/                              the verdict; EvalProposal end to end against a stand-in EmployeeRun
```

## Rules (all microservices)
- Owns exactly one database and one role; never reads another service's tables.
- Every state change writes its event to the `outbox` table in the same transaction.
- Every consumer dedupes through the `inbox` table.
- Every call has a deadline; every mutating call has an idempotency key.
