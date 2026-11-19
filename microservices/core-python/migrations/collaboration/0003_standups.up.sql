-- Standups: run by the co-CEO over real task status; the person can join the discussion.
-- A standup is also a conversation (standup_id = conversation_id), so every update, every
-- word of the discussion and the summary can be read like any other conversation.

CREATE TABLE standups (
    tenant_id         uuid        NOT NULL,
    standup_id        uuid        NOT NULL,
    department_id     text        NOT NULL DEFAULT '',   -- empty: the whole company
    status            text        NOT NULL DEFAULT 'collecting'
                      CHECK (status IN ('collecting', 'discussing', 'summarising', 'done', 'failed')),
    led_by            text        NOT NULL DEFAULT '',   -- the co-CEO, once the roster is read
    started_by        text        NOT NULL,
    discussion_s      integer     NOT NULL CHECK (discussion_s BETWEEN 0 AND 3600),
    assign_next_steps boolean     NOT NULL DEFAULT false,
    reports           jsonb       NOT NULL DEFAULT '[]',
    summary           jsonb,
    error             text        NOT NULL DEFAULT '',
    idempotency_key   text        NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    PRIMARY KEY (tenant_id, standup_id),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, standup_id) REFERENCES conversations (tenant_id, conversation_id)
);

CREATE INDEX standups_recent ON standups (tenant_id, department_id, created_at DESC);

ALTER TABLE standups ENABLE ROW LEVEL SECURITY;
ALTER TABLE standups FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON standups
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

GRANT SELECT, INSERT, UPDATE ON standups TO wfk_app;
