-- sandbox_db: no business data. One row per execution, so a retried run returns its first
-- outcome instead of running the code again, and its usage event goes through the outbox.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_app') THEN
        CREATE ROLE wfk_app NOLOGIN;
    END IF;
END
$$;

CREATE TABLE executions (
    id          uuid        PRIMARY KEY,        -- from the caller's idempotency key
    tenant_id   uuid        NOT NULL,
    run_id      text        NOT NULL DEFAULT '',
    image       text        NOT NULL,
    exit_code   bigint      NOT NULL,
    timed_out   boolean     NOT NULL,
    duration_ms bigint      NOT NULL,
    outcome     jsonb       NOT NULL,            -- stdout and stderr are clipped (256 KiB each)
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX executions_run ON executions (tenant_id, run_id);

ALTER TABLE executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE executions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON executions
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

GRANT SELECT, INSERT ON executions TO wfk_app;
GRANT SELECT, INSERT, UPDATE ON outbox, inbox TO wfk_app;
GRANT USAGE ON SEQUENCE outbox_seq_seq TO wfk_app;
