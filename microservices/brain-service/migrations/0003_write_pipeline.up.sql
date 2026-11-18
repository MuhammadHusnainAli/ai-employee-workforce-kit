-- brain_db, step 2 (brain.md section 4): the governed write path.

-- Quarantined memories (instructions from outside, low-trust contradictions) wait for a person
-- in the Brain Inspector and are invisible to recall until approved. Rejected ones stay as a
-- record of what was refused.
ALTER TABLE memories
    ADD COLUMN status        text NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active', 'quarantined', 'rejected')),
    -- Facts and preferences: subject.attribute ("ana.meeting_day"). A newer value of the same
    -- key supersedes the older one.
    ADD COLUMN fact_key      text,
    -- The memory that replaced this one (its validity was closed when it was superseded).
    ADD COLUMN superseded_by uuid;

CREATE INDEX memories_fact_key ON memories (tenant_id, employee_id, fact_key)
    WHERE fact_key IS NOT NULL AND valid_to IS NULL;
CREATE INDEX memories_review ON memories (tenant_id, employee_id, recorded_at DESC)
    WHERE status = 'quarantined';

-- One row per source event: the write path is idempotent on it.
CREATE TABLE ingest_log (
    tenant_id       uuid        NOT NULL,
    source_event_id uuid        NOT NULL,
    event_type      text        NOT NULL,
    employee_id     text        NOT NULL,
    memory_ids      uuid[]      NOT NULL DEFAULT '{}',
    counts          jsonb       NOT NULL DEFAULT '{}',  -- inserted, reinforced, superseded, ...
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source_event_id)
);

-- Per-tenant write-path policy. No row: the service default.
CREATE TABLE tenant_policies (
    tenant_id  uuid        PRIMARY KEY,
    pii_policy text        NOT NULL CHECK (pii_policy IN ('keep', 'redact', 'drop')),
    updated_at timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['ingest_log', 'tenant_policies'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I'
            ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
            ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON %I TO wfk_app', t);
    END LOOP;
END
$$;

-- The Brain Inspector can delete a memory (and its episode row).
GRANT DELETE ON memories, episodes TO wfk_app;
