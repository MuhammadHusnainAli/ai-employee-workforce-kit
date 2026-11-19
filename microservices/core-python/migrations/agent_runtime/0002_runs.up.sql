-- The run ledger: one row per run, one row per recorded step (agent-harness.md, stage 5).

-- The role the service runs as. Not a superuser, no BYPASSRLS, so RLS applies.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_app') THEN
        CREATE ROLE wfk_app NOLOGIN;
    END IF;
END
$$;

CREATE TABLE runs (
    run_id        text        PRIMARY KEY,           -- the EmployeeRun workflow id
    tenant_id     uuid        NOT NULL,
    employee_id   text        NOT NULL,
    task          text        NOT NULL,
    status        text        NOT NULL
                  CHECK (status IN ('running', 'waiting_human', 'completed', 'failed')),
    stop_reason   text        NOT NULL DEFAULT '',
    output        text,
    steps         integer     NOT NULL DEFAULT 0,
    input_tokens  bigint      NOT NULL DEFAULT 0,
    output_tokens bigint      NOT NULL DEFAULT 0,
    cost_usd      numeric(14, 6) NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX runs_tenant ON runs (tenant_id, created_at DESC);
CREATE INDEX runs_tenant_status ON runs (tenant_id, status) WHERE status IN ('running', 'waiting_human');

CREATE TABLE run_steps (
    run_id        text        NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    seq           integer     NOT NULL,              -- ledger entry number, assigned by the workflow
    tenant_id     uuid        NOT NULL,
    step          integer     NOT NULL,              -- AgentState.step after the entry
    kind          text        NOT NULL,              -- final, tool_calls, repair, ask_human, give_up,
                                                     -- approved, declined, reply, budget, cancelled
    decision      jsonb       NOT NULL,
    results       jsonb       NOT NULL DEFAULT '[]',
    model         text,
    prompt_hash   text,                              -- reproduce any step: same hash, same prompt
    input_tokens  integer     NOT NULL DEFAULT 0,
    output_tokens integer     NOT NULL DEFAULT 0,
    cost_usd      numeric(14, 6) NOT NULL DEFAULT 0,
    status_after  text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, seq)                        -- a retried write cannot add a second row
);

CREATE INDEX run_steps_tenant ON run_steps (tenant_id, run_id, seq);

ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runs
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

ALTER TABLE run_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_steps FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON run_steps
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

GRANT SELECT, INSERT, UPDATE ON runs, run_steps TO wfk_app;
GRANT SELECT, INSERT, UPDATE ON outbox, inbox TO wfk_app;
GRANT USAGE ON SEQUENCE outbox_seq_seq TO wfk_app;
