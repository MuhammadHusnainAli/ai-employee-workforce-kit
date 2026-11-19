UPDATE memories SET status = 'active' WHERE status = 'archived';
ALTER TABLE memories DROP CONSTRAINT memories_status_check;
ALTER TABLE memories ADD CONSTRAINT memories_status_check
    CHECK (status IN ('active', 'quarantined', 'rejected'));
DROP TABLE credit_log;
