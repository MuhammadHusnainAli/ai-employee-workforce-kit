-- The pattern every tenant-owned table follows. Replace `examples` with the service's own
-- tables, and delete this file in a real service.

-- The role the service runs as. It is not a superuser and has no BYPASSRLS, so RLS applies.
-- In production the platform creates one role per service; locally this keeps tests honest.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_app') THEN
        CREATE ROLE wfk_app NOLOGIN;
    END IF;
END
$$;

CREATE TABLE examples (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid        NOT NULL,
    name       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Every query is filtered by tenant first, so the tenant column leads every index.
CREATE INDEX examples_tenant ON examples (tenant_id, created_at DESC);

-- Row-level security: rows are visible and writable only for app.tenant_id, which
-- wfk-platform sets per transaction (Database.tenant_tx). FORCE applies it to the owner too.
ALTER TABLE examples ENABLE ROW LEVEL SECURITY;
ALTER TABLE examples FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON examples
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON examples TO wfk_app;
GRANT SELECT, INSERT, UPDATE ON outbox, inbox TO wfk_app;
GRANT USAGE ON SEQUENCE outbox_seq_seq TO wfk_app;
