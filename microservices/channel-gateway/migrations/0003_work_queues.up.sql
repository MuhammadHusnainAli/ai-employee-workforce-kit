-- Work for the background loops, across tenants. Ids only: the rows they point at are read
-- under row-level security in a tenant transaction, so no loop ever sees tenant data without
-- naming the tenant. A row leaves its queue when its work is done or has failed for good.

CREATE TABLE forward_queue (
    inbound_id      uuid        PRIMARY KEY REFERENCES inbound_messages (id) ON DELETE CASCADE,
    tenant_id       uuid        NOT NULL,
    next_attempt_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE delivery_queue (
    delivery_id     uuid        PRIMARY KEY REFERENCES deliveries (id) ON DELETE CASCADE,
    tenant_id       uuid        NOT NULL,
    next_attempt_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX forward_queue_due ON forward_queue (next_attempt_at);
CREATE INDEX delivery_queue_due ON delivery_queue (next_attempt_at);

-- What each delivery answers, so a redelivered run event adds nothing.
CREATE UNIQUE INDEX deliveries_once ON deliveries (inbound_id, purpose, md5(text))
    WHERE inbound_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE, DELETE ON forward_queue, delivery_queue TO wfk_app;
