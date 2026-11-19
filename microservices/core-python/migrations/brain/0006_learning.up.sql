-- brain_db, step 5 (brain.md section 6): learning from outcomes.

-- One row per run whose outcome credited (or blamed) the memories it recalled: once only.
CREATE TABLE credit_log (
    tenant_id  uuid        NOT NULL,
    run_id     text        NOT NULL,
    outcome    text        NOT NULL,
    credited   integer     NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, run_id)
);

-- Archived memories decayed below the floor: kept, never recalled.
ALTER TABLE memories DROP CONSTRAINT memories_status_check;
ALTER TABLE memories ADD CONSTRAINT memories_status_check
    CHECK (status IN ('active', 'quarantined', 'rejected', 'archived'));

ALTER TABLE credit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON credit_log
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT, UPDATE ON credit_log TO wfk_app;
