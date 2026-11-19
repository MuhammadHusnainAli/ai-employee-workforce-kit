-- task_db: the work inbox. A task's status is a projection of its run's events.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_app') THEN
        CREATE ROLE wfk_app NOLOGIN;
    END IF;
END
$$;

CREATE TABLE tasks (
    task_id         uuid        PRIMARY KEY,          -- also the EmployeeRun workflow id
    tenant_id       uuid        NOT NULL,
    employee_id     text        NOT NULL,
    title           text        NOT NULL,
    input           text        NOT NULL,
    priority        integer     NOT NULL DEFAULT 0 CHECK (priority BETWEEN 0 AND 9),
    status          text        NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'waiting_human', 'completed',
                                      'failed', 'cancelled')),
    stop_reason     text        NOT NULL DEFAULT '',
    run_seq         integer     NOT NULL DEFAULT -2,  -- last run event applied (-1 = started)
    idempotency_key text        NOT NULL,
    created_by      text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX tasks_inbox ON tasks (tenant_id, status, priority DESC, created_at);
CREATE INDEX tasks_employee ON tasks (tenant_id, employee_id, created_at DESC);

ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tasks
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

GRANT SELECT, INSERT, UPDATE ON tasks TO wfk_app;
GRANT SELECT, INSERT, UPDATE ON outbox, inbox TO wfk_app;
GRANT USAGE ON SEQUENCE outbox_seq_seq TO wfk_app;
