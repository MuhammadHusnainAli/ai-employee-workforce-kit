-- channel_db: channel bindings, the inbound message log and delivery receipts.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_app') THEN
        CREATE ROLE wfk_app NOLOGIN;
    END IF;
END
$$;

-- A channel connected to an employee: a Slack app, a Teams outgoing webhook, a Google Chat
-- app, a mail relay or a generic webhook.
CREATE TABLE bindings (
    id          uuid        PRIMARY KEY,
    tenant_id   uuid        NOT NULL,
    kind        text        NOT NULL
                CHECK (kind IN ('slack', 'teams', 'google_chat', 'email', 'webhook')),
    employee_id text        NOT NULL,
    name        text        NOT NULL,
    -- Verifies inbound requests: the signing secret (Slack, email, webhook), the base64
    -- security token (Teams) or the app's project number, the JWT audience (Google Chat).
    -- Locally a column; in production a reference into the secret store.
    secret      text        NOT NULL,
    outbound    jsonb       NOT NULL DEFAULT '{}',   -- {"url", "token", "from"}
    active      boolean     NOT NULL DEFAULT true,
    created_by  text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Webhooks arrive without a tenant: this maps the binding in the URL to its tenant, so the
-- binding itself is then read under row-level security. Ids only, nothing else.
CREATE TABLE hook_routes (
    binding_id uuid PRIMARY KEY REFERENCES bindings (id) ON DELETE CASCADE,
    tenant_id  uuid NOT NULL
);

-- Every message received. The provider's message id is unique per binding: a webhook
-- delivered twice is stored once.
CREATE TABLE inbound_messages (
    id                  uuid        PRIMARY KEY,
    tenant_id           uuid        NOT NULL,
    binding_id          uuid        NOT NULL REFERENCES bindings (id),
    provider_message_id text        NOT NULL,
    thread              text        NOT NULL,
    sender              text        NOT NULL,
    text                text        NOT NULL,
    status              text        NOT NULL DEFAULT 'received'
                        CHECK (status IN ('received', 'forwarded', 'failed')),
    task_id             uuid,
    attempts            integer     NOT NULL DEFAULT 0,
    last_error          text,
    received_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (binding_id, provider_message_id)
);

CREATE INDEX inbound_pending ON inbound_messages (received_at) WHERE status = 'received';
CREATE INDEX inbound_task ON inbound_messages (task_id);

-- Every reply sent back, with its receipt. One per (message, what it says): a redelivered run
-- event sends nothing twice.
CREATE TABLE deliveries (
    id           uuid        PRIMARY KEY,
    tenant_id    uuid        NOT NULL,
    binding_id   uuid        NOT NULL REFERENCES bindings (id),
    inbound_id   uuid        REFERENCES inbound_messages (id),
    task_id      uuid,
    thread       text        NOT NULL,
    purpose      text        NOT NULL,              -- answer, approval_request, failure, message
    text         text        NOT NULL,
    status       text        NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'sent', 'failed')),
    attempts     integer     NOT NULL DEFAULT 0,
    last_error   text,
    provider_ref text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    sent_at      timestamptz
);

CREATE INDEX deliveries_pending ON deliveries (created_at) WHERE status = 'pending';

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['bindings', 'inbound_messages', 'deliveries'] LOOP
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

GRANT SELECT, INSERT, DELETE ON hook_routes TO wfk_app;
GRANT SELECT, INSERT, UPDATE ON outbox, inbox TO wfk_app;
GRANT USAGE ON SEQUENCE outbox_seq_seq TO wfk_app;
