DROP TABLE skills;
DELETE FROM change_proposals WHERE kind = 'skill_add';
ALTER TABLE change_proposals DROP CONSTRAINT change_proposals_kind_check;
ALTER TABLE change_proposals ADD CONSTRAINT change_proposals_kind_check
    CHECK (kind IN ('rule_add'));
ALTER TABLE episodes DROP COLUMN tools;
