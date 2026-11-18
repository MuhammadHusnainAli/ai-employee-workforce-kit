-- brain_db, step 5 (brain.md section 5): nightly consolidation.

-- Per employee: how far consolidation has read, and what the last pass did.
CREATE TABLE consolidations (
    tenant_id       uuid        NOT NULL,
    employee_id     text        NOT NULL,
    watermark       timestamptz NOT NULL DEFAULT '-infinity',  -- episodes up to here are done
    last_run_at     timestamptz,
    counts          jsonb       NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, employee_id)
);

ALTER TABLE consolidations ENABLE ROW LEVEL SECURITY;
ALTER TABLE consolidations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON consolidations
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT, UPDATE ON consolidations TO wfk_app;

-- Who has new episodes. Ids only, so the nightly schedule can find them across tenants without
-- reading tenant data; each consolidation then runs in its tenant's transaction.
CREATE TABLE consolidation_due (
    tenant_id   uuid        NOT NULL,
    employee_id text        NOT NULL,
    since       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, employee_id)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON consolidation_due TO wfk_app;

-- Entities merged into another one are deleted; their links and relations move first.
GRANT DELETE ON entities TO wfk_app;
