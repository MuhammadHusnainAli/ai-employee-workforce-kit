-- The org chart: departments, titles, reporting lines and the co-CEO.
-- The person who owns the workspace is the CEO and is not an employee. Exactly one employee may
-- be the co-CEO; it reports to the person. Anyone else without a manager reports to the person.

CREATE TABLE departments (
    tenant_id        uuid        NOT NULL,
    department_id    text        NOT NULL CHECK (department_id ~ '^[a-z][a-z0-9-]{1,62}$'),
    name             text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    head_employee_id text,
    created_by       text        NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, department_id),
    FOREIGN KEY (tenant_id, head_employee_id) REFERENCES employees (tenant_id, employee_id)
);

ALTER TABLE employees
    ADD COLUMN title         text    NOT NULL DEFAULT '' CHECK (length(title) <= 200),
    ADD COLUMN department_id text,
    ADD COLUMN manager_id    text,
    ADD COLUMN is_co_ceo     boolean NOT NULL DEFAULT false,
    ADD FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, department_id),
    ADD FOREIGN KEY (tenant_id, manager_id) REFERENCES employees (tenant_id, employee_id),
    ADD CHECK (NOT (is_co_ceo AND manager_id IS NOT NULL)),
    ADD CHECK (manager_id IS DISTINCT FROM employee_id);

-- One co-CEO per workspace.
CREATE UNIQUE INDEX employees_one_co_ceo ON employees (tenant_id) WHERE is_co_ceo;
CREATE INDEX employees_manager ON employees (tenant_id, manager_id);

ALTER TABLE departments ENABLE ROW LEVEL SECURITY;
ALTER TABLE departments FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON departments
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

GRANT SELECT, INSERT, UPDATE ON departments TO wfk_app;
