-- eval_db: one evaluation per proposed brain change, with both reports and the verdict.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_app') THEN
        CREATE ROLE wfk_app NOLOGIN;
    END IF;
END
$$;

CREATE TABLE eval_runs (
    id          uuid        PRIMARY KEY,           -- from the proposal: one evaluation each
    tenant_id   uuid        NOT NULL,
    employee_id text        NOT NULL,
    proposal_id text        NOT NULL,
    status      text        NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'passed', 'failed')),
    baseline    jsonb,                             -- EvalReport without the change
    candidate   jsonb,                             -- EvalReport with it
    comparison  jsonb,                             -- fixed, regressed, scores
    reasons     text[]      NOT NULL DEFAULT '{}', -- why it failed, in words
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE INDEX eval_runs_employee ON eval_runs (tenant_id, employee_id, created_at DESC);

ALTER TABLE eval_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON eval_runs
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

GRANT SELECT, INSERT, UPDATE ON eval_runs TO wfk_app;
GRANT SELECT, INSERT, UPDATE ON outbox, inbox TO wfk_app;
GRANT USAGE ON SEQUENCE outbox_seq_seq TO wfk_app;
