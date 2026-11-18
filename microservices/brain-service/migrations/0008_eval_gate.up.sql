-- Proposals wait for their evaluation (evaluation-service): no passing report, no approval.
ALTER TABLE change_proposals
    ADD COLUMN eval_status  text NOT NULL DEFAULT 'pending'
                            CHECK (eval_status IN ('pending', 'passed', 'failed')),
    ADD COLUMN eval_reasons text[] NOT NULL DEFAULT '{}',
    ADD COLUMN evaluated_at timestamptz;
