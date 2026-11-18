-- Run modes: live work, evaluation runs, and shadow runs of a candidate brain change.
ALTER TABLE runs
    ADD COLUMN mode      text NOT NULL DEFAULT 'live' CHECK (mode IN ('live', 'eval', 'shadow')),
    ADD COLUMN candidate text NOT NULL DEFAULT '';
