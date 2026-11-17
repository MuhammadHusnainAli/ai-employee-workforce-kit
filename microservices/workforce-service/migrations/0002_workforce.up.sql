-- workforce_db: AI employees and their immutable versions.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_app') THEN
        CREATE ROLE wfk_app NOLOGIN;
    END IF;
END
$$;

CREATE TABLE employees (
    tenant_id      uuid        NOT NULL,
    employee_id    text        NOT NULL CHECK (employee_id ~ '^[a-z][a-z0-9-]{1,62}$'),
    name           text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    status         text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    active_version integer,
    created_by     text        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, employee_id)
);

-- A version is a blueprint: role, instructions, model, tool grants and limits. Runs pin one at
-- start, so it must never change. Publishing creates a new version; rollback re-activates an
-- old one.
CREATE TABLE employee_versions (
    tenant_id       uuid        NOT NULL,
    employee_id     text        NOT NULL,
    version         integer     NOT NULL CHECK (version > 0),
    role            text        NOT NULL,
    instructions    text        NOT NULL,
    model           text        NOT NULL,
    tool_grants     jsonb       NOT NULL DEFAULT '[]',   -- [{"tool": "...", "tier": "R0".."R3"}]
    auto_approve    text[]      NOT NULL DEFAULT '{}',   -- R2 tools allowed without approval
    limits          jsonb       NOT NULL DEFAULT '{}',
    idempotency_key text        NOT NULL,
    created_by      text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, employee_id, version),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES employees (tenant_id, employee_id),
    UNIQUE (tenant_id, idempotency_key)
);

ALTER TABLE employees ADD FOREIGN KEY (tenant_id, employee_id, active_version)
    REFERENCES employee_versions (tenant_id, employee_id, version);

CREATE FUNCTION employee_versions_are_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'employee versions are immutable: publish a new version instead'
        USING ERRCODE = 'restrict_violation';
END
$$;

CREATE TRIGGER employee_versions_immutable
    BEFORE UPDATE OR DELETE ON employee_versions
    FOR EACH ROW EXECUTE FUNCTION employee_versions_are_immutable();

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['employees', 'employee_versions'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I'
            ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
            ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
    END LOOP;
END
$$;

GRANT SELECT, INSERT, UPDATE ON employees TO wfk_app;
GRANT SELECT, INSERT ON employee_versions TO wfk_app;
GRANT SELECT, INSERT, UPDATE ON outbox, inbox TO wfk_app;
GRANT USAGE ON SEQUENCE outbox_seq_seq TO wfk_app;
