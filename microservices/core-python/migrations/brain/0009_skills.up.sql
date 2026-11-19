-- brain_db, step 5 (brain.md section 5): skills mined from repeated successful work.

-- Which tools a run used, in order: what skill mining looks for.
ALTER TABLE episodes ADD COLUMN tools text[] NOT NULL DEFAULT '{}';

-- A skill is proposed like a rule and becomes active only when a person approves it.
ALTER TABLE change_proposals DROP CONSTRAINT change_proposals_kind_check;
ALTER TABLE change_proposals ADD CONSTRAINT change_proposals_kind_check
    CHECK (kind IN ('rule_add', 'skill_add'));

-- A known way of doing one kind of task: when the task looks like `trigger`, use `tools` in
-- this order. Recalled by how close the task is to the trigger.
CREATE TABLE skills (
    id           uuid        PRIMARY KEY,
    tenant_id    uuid        NOT NULL,
    employee_id  text        NOT NULL,
    name         text        NOT NULL,
    trigger      text        NOT NULL,
    tools        text[]      NOT NULL CHECK (cardinality(tools) > 0),
    procedure    text        NOT NULL,
    embedding    vector(1024),
    status       text        NOT NULL DEFAULT 'proposed'
                 CHECK (status IN ('proposed', 'active', 'rejected', 'retired')),
    proposal_id  uuid        REFERENCES change_proposals (id),
    evidence     uuid[]      NOT NULL DEFAULT '{}',   -- the episodes it was mined from
    approved_by  text,
    use_count    integer     NOT NULL DEFAULT 0,
    last_used_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- One skill per tool sequence: mining the same pattern again proposes nothing new.
    UNIQUE (tenant_id, employee_id, tools)
);

CREATE INDEX skills_active ON skills (tenant_id, employee_id) WHERE status = 'active';

ALTER TABLE skills ENABLE ROW LEVEL SECURITY;
ALTER TABLE skills FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON skills
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT, UPDATE ON skills TO wfk_app;
