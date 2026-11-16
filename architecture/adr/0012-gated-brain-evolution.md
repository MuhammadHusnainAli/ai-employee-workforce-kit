# ADR-0012: Brain evolution through versioned, evaluated, reversible proposals

**Status:** Accepted

## Context
Employees must learn from corrections, outcomes and reviews. Unchecked self-modification causes drift, regressions
and poisoning, because one bad memory or skill can change behaviour across every future task.

## Decision
- Facts below a risk threshold auto-apply with provenance and trust tags.
- Every **behaviour** change goes through the same pipeline: mine → `change_proposal` → offline eval (role golden
  set, the employee's regression set, safety suite; no metric may regress) → human approval (identity, rules,
  high-risk skills) → shadow (≥ 50 tasks or 7 days) → canary (10 %) → promote. Rollback is available at every step.
  Behaviour changes are new skills, new rules and identity edits.
- Nightly consolidation and credit assignment (via `recall_log`) adjust importance, demote misleading memories and
  update skill statistics.
- An **autonomy ladder** (L0–L4) grants autonomy from measured performance and demotes automatically on incidents or
  eval drops. R3 actions always need a human.

## Consequences
- Employees improve continuously, and every behaviour change is attributable, tested and reversible.
- An Evaluation Service and per-role golden sets become required platform components.
