-- Hire requests. A hire costs money, so an employee can only ask; a person decides. Approving
-- creates the employee, its first version and its place in one transaction.

CREATE TABLE hire_requests (
    tenant_id         uuid        NOT NULL,
    request_id        uuid        NOT NULL,
    requested_by      text        NOT NULL,
    requested_by_kind text        NOT NULL CHECK (requested_by_kind IN ('person', 'employee', 'service')),
    hire              jsonb       NOT NULL,   -- the Hire: who, where, role, instructions, model
    reason            text        NOT NULL DEFAULT '',
    status            text        NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by        text,
    decision_note     text        NOT NULL DEFAULT '',
    idempotency_key   text        NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    decided_at        timestamptz,
    PRIMARY KEY (tenant_id, request_id),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX hire_requests_status ON hire_requests (tenant_id, status, created_at DESC);

ALTER TABLE hire_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE hire_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON hire_requests
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

GRANT SELECT, INSERT, UPDATE ON hire_requests TO wfk_app;
