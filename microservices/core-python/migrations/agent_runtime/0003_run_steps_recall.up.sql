-- Cite the brain recall each step used (brain.md: "every recall is cited in run_steps"),
-- and flag steps that ran with degraded memory. Expand only: both columns are optional.
ALTER TABLE run_steps ADD COLUMN recall_id uuid;
ALTER TABLE run_steps ADD COLUMN memory_degraded boolean NOT NULL DEFAULT false;
