#!/usr/bin/env bash
# PostgreSQL first-start initialisation (mounted in /docker-entrypoint-initdb.d).
# One database and one owner role per service (least privilege), plus a read-only role
# used by the Dagster code location (custom pipelines) and Grafana (audit dashboards).
set -euo pipefail

: "${DAGSTER_PG_PASSWORD:?}" "${KEYCLOAK_DB_PASSWORD:?}" "${DATAHUB_APP_DB_PASSWORD:?}" \
  "${DATAHUB_RO_PASSWORD:?}" "${GRAFANA_DB_PASSWORD:?}"

psql_admin() {
  psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB:-postgres}" "$@"
}

create_role_and_db() {
  local role="$1" password="$2" db="$3"
  psql_admin -v role="${role}" -v password="${password}" -v db="${db}" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'role', :'password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db') \gexec
SQL
}

create_role_and_db dagster     "${DAGSTER_PG_PASSWORD}"     dagster
create_role_and_db keycloak    "${KEYCLOAK_DB_PASSWORD}"    keycloak
create_role_and_db datahub_app "${DATAHUB_APP_DB_PASSWORD}" datahub
create_role_and_db grafana     "${GRAFANA_DB_PASSWORD}"     grafana

# Read-only role on the Data Hub application database (grants on tables are applied by
# the application migrations, see services/datahub-ui/datahub_ui/db/migrations/002_grants.sql).
psql_admin -v password="${DATAHUB_RO_PASSWORD}" <<'SQL'
SELECT format('CREATE ROLE datahub_ro LOGIN PASSWORD %L', :'password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datahub_ro') \gexec
SELECT format('ALTER ROLE datahub_ro PASSWORD %L', :'password') \gexec
GRANT CONNECT ON DATABASE datahub TO datahub_ro;
SQL

# The application role owns its schema (created by the migrations).
psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname datahub <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CREATE ON DATABASE datahub TO datahub_app;
SQL

echo "Data Hub databases initialised: dagster, keycloak, datahub, grafana"
