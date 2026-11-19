-- collab_db: conversations between employees and with the person. Every message is kept, so the
-- person can read every direct conversation, call and standup.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_app') THEN
        CREATE ROLE wfk_app NOLOGIN;
    END IF;
END
$$;

-- direct: two participants, one conversation per pair, reused.
-- call: a short question and answer exchange, capped at max_turns, then closed.
-- standup: run by the co-CEO; the person can join.
CREATE TABLE conversations (
    tenant_id       uuid        NOT NULL,
    conversation_id uuid        NOT NULL,
    kind            text        NOT NULL CHECK (kind IN ('direct', 'call', 'standup')),
    topic           text        NOT NULL DEFAULT '' CHECK (length(topic) <= 500),
    status          text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    started_by      text        NOT NULL,
    pair_key        text,                      -- direct only: the two participants, sorted
    turns           integer     NOT NULL DEFAULT 0,
    max_turns       integer     NOT NULL DEFAULT 0,  -- 0: no cap
    last_seq        integer     NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    closed_at       timestamptz,
    PRIMARY KEY (tenant_id, conversation_id),
    UNIQUE (tenant_id, pair_key)
);

CREATE INDEX conversations_recent ON conversations (tenant_id, updated_at DESC);

-- participant: an employee id, or 'person' for the person who owns the workspace.
CREATE TABLE participants (
    tenant_id       uuid    NOT NULL,
    conversation_id uuid    NOT NULL,
    participant     text    NOT NULL,
    kind            text    NOT NULL CHECK (kind IN ('employee', 'person')),
    last_read_seq   integer NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, conversation_id, participant),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, conversation_id)
);

CREATE INDEX participants_by_who ON participants (tenant_id, participant);

CREATE TABLE messages (
    tenant_id       uuid        NOT NULL,
    conversation_id uuid        NOT NULL,
    seq             integer     NOT NULL CHECK (seq > 0),
    message_id      uuid        NOT NULL,
    author          text        NOT NULL,
    author_kind     text        NOT NULL CHECK (author_kind IN ('employee', 'person', 'system')),
    kind            text        NOT NULL
                    CHECK (kind IN ('message', 'question', 'answer', 'report', 'summary', 'system')),
    text            text        NOT NULL CHECK (length(text) BETWEEN 1 AND 20000),
    idempotency_key text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, conversation_id, seq),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, conversation_id)
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['conversations', 'participants', 'messages'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I'
            ' USING (tenant_id = current_setting(''app.tenant_id'')::uuid)'
            ' WITH CHECK (tenant_id = current_setting(''app.tenant_id'')::uuid)', t);
    END LOOP;
END
$$;

GRANT SELECT, INSERT, UPDATE ON conversations, participants, messages TO wfk_app;
GRANT SELECT, INSERT, UPDATE ON outbox, inbox TO wfk_app;
GRANT USAGE ON SEQUENCE outbox_seq_seq TO wfk_app;
