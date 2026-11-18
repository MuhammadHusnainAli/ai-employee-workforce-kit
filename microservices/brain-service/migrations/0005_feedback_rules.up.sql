-- brain_db, step 4 (brain.md section 6): feedback, rules from verified corrections, approvals.

-- Every piece of feedback, as given. Idempotent per tenant on the caller's key.
CREATE TABLE feedback (
    id              uuid        PRIMARY KEY,
    tenant_id       uuid        NOT NULL,
    employee_id     text        NOT NULL,
    run_id          text,
    kind            text        NOT NULL
                    CHECK (kind IN ('correction', 'rating', 'approval_rejected', 'comment')),
    text            text        NOT NULL DEFAULT '',
    rating          smallint    CHECK (rating IN (-1, 1)),
    actor_id        text        NOT NULL,
    actor_kind      text        NOT NULL CHECK (actor_kind IN ('human', 'employee', 'service')),
    idempotency_key text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX feedback_employee ON feedback (tenant_id, employee_id, created_at DESC);

-- A proposed change to an employee's brain, waiting for a person (brain.md section 6, step 4).
CREATE TABLE change_proposals (
    id            uuid        PRIMARY KEY,
    tenant_id     uuid        NOT NULL,
    employee_id   text        NOT NULL,
    kind          text        NOT NULL CHECK (kind IN ('rule_add')),
    payload       jsonb       NOT NULL,             -- {"text": "..."}
    evidence      jsonb       NOT NULL DEFAULT '{}', -- {"feedback_id", "run_id"}
    risk          text        NOT NULL CHECK (risk IN ('low', 'medium', 'high')),
    status        text        NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected')),
    proposed_by   text        NOT NULL,
    decided_by    text,
    decision_note text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    decided_at    timestamptz
);

CREATE INDEX change_proposals_pending ON change_proposals (tenant_id, employee_id, created_at)
    WHERE status = 'pending';

-- Rules: always human-verified, pinned in every recall while active.
CREATE TABLE policy_rules (
    id          uuid        PRIMARY KEY,
    tenant_id   uuid        NOT NULL,
    employee_id text,                         -- NULL: a rule for every employee of the tenant
    text        text        NOT NULL,
    status      text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    trust       text        NOT NULL DEFAULT 'human_verified' CHECK (trust = 'human_verified'),
    proposal_id uuid        REFERENCES change_proposals (id),
    approved_by text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_by  text,
    revoked_at  timestamptz
);

CREATE INDEX policy_rules_active ON policy_rules (tenant_id, employee_id) WHERE status = 'active';

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['feedback', 'change_proposals', 'policy_rules'] LOOP
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
