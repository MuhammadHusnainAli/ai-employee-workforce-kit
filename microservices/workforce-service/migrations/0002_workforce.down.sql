-- The two tables reference each other (active_version), so drop with CASCADE.
DROP TABLE IF EXISTS employee_versions CASCADE;
DROP TABLE IF EXISTS employees CASCADE;
DROP FUNCTION IF EXISTS employee_versions_are_immutable();
