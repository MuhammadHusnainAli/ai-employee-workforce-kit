DROP INDEX IF EXISTS tasks_root;
DROP INDEX IF EXISTS tasks_parent;
ALTER TABLE tasks
    DROP COLUMN IF EXISTS delegated_by,
    DROP COLUMN IF EXISTS output,
    DROP COLUMN IF EXISTS depth,
    DROP COLUMN IF EXISTS root_task_id,
    DROP COLUMN IF EXISTS parent_task_id;
