-- Delegation: a task can be handed down from another. The goal at the top is depth 0; a
-- sub-task is one deeper than its parent, at most 3. The run's final answer is kept, so it can
-- travel up to the parent.

ALTER TABLE tasks
    ADD COLUMN parent_task_id uuid REFERENCES tasks (task_id),
    ADD COLUMN root_task_id   uuid,
    ADD COLUMN depth          smallint NOT NULL DEFAULT 0 CHECK (depth BETWEEN 0 AND 3),
    ADD COLUMN output         text     NOT NULL DEFAULT '',
    ADD COLUMN delegated_by   text     NOT NULL DEFAULT '',
    ADD CHECK ((parent_task_id IS NULL) = (depth = 0));

CREATE INDEX tasks_parent ON tasks (tenant_id, parent_task_id, created_at)
    WHERE parent_task_id IS NOT NULL;
CREATE INDEX tasks_root ON tasks (tenant_id, root_task_id, created_at)
    WHERE root_task_id IS NOT NULL;
