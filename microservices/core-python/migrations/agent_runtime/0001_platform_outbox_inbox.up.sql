-- Outbox and inbox tables. Every service database has both.
-- Copied into each service's first migration (see templates/), and used by wfk-platform's tests.

-- Events written in the same transaction as the state change they describe.
-- The relay publishes rows in `seq` order and stamps `published_at`.
-- No RLS: only the owning service writes here, and the relay reads across tenants.
CREATE TABLE IF NOT EXISTS outbox (
    seq          bigserial   PRIMARY KEY,
    id           uuid        NOT NULL UNIQUE,          -- CloudEvent id, sent as Nats-Msg-Id
    tenant_id    uuid        NOT NULL,
    type         text        NOT NULL,                 -- equals the NATS subject
    source       text        NOT NULL,
    subject      text,
    time         timestamptz NOT NULL,
    traceparent  text,
    data         jsonb       NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);

CREATE INDEX IF NOT EXISTS outbox_unpublished ON outbox (seq) WHERE published_at IS NULL;

-- Events this service has already processed, per consumer. A redelivered event finds its
-- row and is skipped, so handlers run once even though delivery is at least once.
CREATE TABLE IF NOT EXISTS inbox (
    consumer     text        NOT NULL,
    event_id     uuid        NOT NULL,
    tenant_id    uuid        NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, event_id)
);
