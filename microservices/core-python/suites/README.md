# Suites

- `golden/<employee_id>.yaml` (or `golden/default.yaml`): what good work looks like for the role.
- `safety/*.yaml`: prompt injection and leaks. Every safety task must pass for a change to be promoted.
- Regression sets are built per employee from its past successful episodes (from brain-service).

A task is `{id, input, expect, weight?, tags?}`; `expect` is agent-harness-adk's `Expect`.
