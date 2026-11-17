-- brain_db, step 1 (brain.md section 9): identity core, memories (episodic, semantic,
-- reflection), episodes and the recall log. Everything is tenant-scoped with forced RLS.

CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_app') THEN
        CREATE ROLE wfk_app NOLOGIN;
    END IF;
END
$$;

-- The identity core, versioned. A behaviour change is a new version, never an edit.
CREATE TABLE brain_versions (
    tenant_id     uuid        NOT NULL,
    employee_id   text        NOT NULL,
    version       integer     NOT NULL CHECK (version > 0),
    status        text        NOT NULL CHECK (status IN ('candidate', 'active', 'retired')),
    identity      jsonb       NOT NULL,   -- role charter, persona, values, style, constraints, escalation
    identity_text text        NOT NULL,   -- the pinned prompt block rendered from `identity`
    created_by    text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, employee_id, version)
);
-- At most one active version per employee.
CREATE UNIQUE INDEX brain_versions_one_active
    ON brain_versions (tenant_id, employee_id) WHERE status = 'active';

-- Long-term memory. Bi-temporal: valid_from / valid_to say when it is true in the world,
-- recorded_at says when the brain learned it. Nothing is overwritten: a newer fact closes
-- the older one's validity.
CREATE TABLE memories (
    id           uuid        PRIMARY KEY,
    tenant_id    uuid        NOT NULL,
    employee_id  text,                   -- NULL for tenant-scope knowledge
    scope        text        NOT NULL CHECK (scope IN ('employee', 'team', 'tenant')),
    kind         text        NOT NULL CHECK (kind IN ('episodic', 'semantic', 'reflection')),
    content      text        NOT NULL,
    embedding    vector(1024),
    tsv          tsvector    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    importance   real        NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
    confidence   real        NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    trust        text        NOT NULL
                 CHECK (trust IN ('human_verified', 'system', 'observed', 'external')),
    source_ref   text,                   -- the event or document it came from
    valid_from   timestamptz NOT NULL DEFAULT now(),
    valid_to     timestamptz,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    use_count    integer     NOT NULL DEFAULT 0,
    metadata     jsonb       NOT NULL DEFAULT '{}',
    CHECK (scope = 'tenant' OR employee_id IS NOT NULL),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX memories_owner ON memories (tenant_id, employee_id, kind) WHERE valid_to IS NULL;
CREATE INDEX memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX memories_tsv ON memories USING gin (tsv);

-- Experiences: one row per task, meeting or chat, with how it went.
CREATE TABLE episodes (
    id          uuid        PRIMARY KEY,
    tenant_id   uuid        NOT NULL,
    employee_id text        NOT NULL,
    memory_id   uuid        NOT NULL REFERENCES memories (id) ON DELETE CASCADE,
    run_id      text,
    task        text        NOT NULL,
    outcome     text        NOT NULL CHECK (outcome IN ('success', 'failure', 'escalated', 'unknown')),
    feedback    text,
    started_at  timestamptz,
    ended_at    timestamptz
);

CREATE INDEX episodes_owner ON episodes (tenant_id, employee_id, ended_at DESC);

-- Every recall, with what it returned and why: citations, and later credit assignment.
CREATE TABLE recall_log (
    id          uuid        PRIMARY KEY,
    tenant_id   uuid        NOT NULL,
    employee_id text        NOT NULL,
    run_id      text,
    step        integer,
    query       text        NOT NULL,
    item_ids    uuid[]      NOT NULL DEFAULT '{}',
    scores      real[]      NOT NULL DEFAULT '{}',
    retrievers  jsonb       NOT NULL DEFAULT '{}',   -- per retriever: hits, ms, skipped
    degraded    boolean     NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX recall_log_run ON recall_log (tenant_id, run_id, step);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['brain_versions', 'memories', 'episodes', 'recall_log'] LOOP
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

GRANT SELECT, INSERT, UPDATE ON outbox, inbox TO wfk_app;
GRANT USAGE ON SEQUENCE outbox_seq_seq TO wfk_app;
