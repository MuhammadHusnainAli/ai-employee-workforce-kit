-- brain_db, step 3 (brain.md sections 2 and 3): the semantic graph.
-- Adjacency tables walked with recursive CTEs (at most 2 hops). Relations are bi-temporal like
-- memories, and each one points at the fact that states it: superseding the fact closes them.

CREATE TABLE entities (
    id          uuid        PRIMARY KEY,
    tenant_id   uuid        NOT NULL,
    employee_id text,                    -- NULL for tenant-scope entities
    name        text        NOT NULL,
    norm_name   text        NOT NULL,    -- lower case, punctuation and "the" stripped
    type        text        NOT NULL
                CHECK (type IN ('person', 'organization', 'project', 'system', 'place', 'other')),
    aliases     text[]      NOT NULL DEFAULT '{}',   -- normalised, like norm_name
    embedding   vector(1024),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX entities_name ON entities (tenant_id, coalesce(employee_id, ''), norm_name);
CREATE INDEX entities_aliases ON entities USING gin (aliases);
CREATE INDEX entities_embedding ON entities USING hnsw (embedding vector_cosine_ops);

CREATE TABLE relations (
    id          uuid        PRIMARY KEY,
    tenant_id   uuid        NOT NULL,
    employee_id text,
    src_id      uuid        NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    type        text        NOT NULL CHECK (type ~ '^[a-z][a-z0-9_]{0,60}$'),
    dst_id      uuid        NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    memory_id   uuid        REFERENCES memories (id) ON DELETE CASCADE,  -- the fact stating it
    confidence  real        NOT NULL DEFAULT 0.8 CHECK (confidence BETWEEN 0 AND 1),
    trust       text        NOT NULL
                CHECK (trust IN ('human_verified', 'system', 'observed', 'external')),
    valid_from  timestamptz NOT NULL DEFAULT now(),
    valid_to    timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (src_id <> dst_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE INDEX relations_src ON relations (tenant_id, src_id) WHERE valid_to IS NULL;
CREATE INDEX relations_dst ON relations (tenant_id, dst_id) WHERE valid_to IS NULL;
CREATE INDEX relations_memory ON relations (memory_id);

-- Which entities a memory is about: graph recall's way back from entities to memories, and
-- Forget(subject)'s way to every memory about a person.
CREATE TABLE memory_entities (
    memory_id uuid NOT NULL REFERENCES memories (id) ON DELETE CASCADE,
    entity_id uuid NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL,
    PRIMARY KEY (memory_id, entity_id)
);

CREATE INDEX memory_entities_entity ON memory_entities (tenant_id, entity_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['entities', 'relations', 'memory_entities'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I'
            ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
            ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO wfk_app', t);
    END LOOP;
END
$$;
