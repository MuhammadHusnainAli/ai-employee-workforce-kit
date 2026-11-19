DROP INDEX IF EXISTS employees_manager;
DROP INDEX IF EXISTS employees_one_co_ceo;
ALTER TABLE employees
    DROP COLUMN IF EXISTS is_co_ceo,
    DROP COLUMN IF EXISTS manager_id,
    DROP COLUMN IF EXISTS department_id,
    DROP COLUMN IF EXISTS title;
DROP TABLE IF EXISTS departments;
