-- Least privilege: the Dagster code location reads pipeline definitions with a read-only role,
-- Grafana reads the audit log. Roles are created by the PostgreSQL init script.
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'datahub_ro') then
        grant usage on schema datahub to datahub_ro;
        grant select on datahub.pipeline_definitions, datahub.audit_log, datahub.incidents to datahub_ro;
    end if;
end
$$;
