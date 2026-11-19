REVOKE DELETE ON memories, episodes FROM wfk_app;
DROP TABLE tenant_policies;
DROP TABLE ingest_log;
DROP INDEX memories_review;
DROP INDEX memories_fact_key;
ALTER TABLE memories DROP COLUMN superseded_by, DROP COLUMN fact_key, DROP COLUMN status;
