#!/usr/bin/env bash
# Restore a backup made by backup.sh:  scripts/restore.sh prod backups/20260101-030000
set -euo pipefail
cd "$(dirname "$0")/.."
ENV="${1:?env}"; SRC="${2:?backup directory}"
COMPOSE=(docker compose --env-file "envs/${ENV}.env" --env-file .env -f docker-compose.yml -f "${COMPOSE_OVERRIDE:-compose/docker-compose.${ENV}.yml}")
read -r -p "Restore ${SRC} on ${ENV}? Current data will be replaced. Type YES: " answer
[[ "$answer" == "YES" ]] || { echo "Aborted."; exit 1; }
"${COMPOSE[@]}" stop dagster-daemon dagster-webserver dagster-code datahub-ui datahub-exporter keycloak grafana
for db in dagster keycloak datahub grafana; do
  echo "pg_restore ${db}"
  "${COMPOSE[@]}" exec -T postgres pg_restore -U postgres --clean --if-exists -d "$db" < "${SRC}/${db}.dump"
done
"${COMPOSE[@]}" run --rm --no-deps -T dagster-code bash -c '
  tar -C /data -xzf - && python - <<"PY"
import os, shutil, duckdb
path = os.environ["DUCKDB_PATH"]
copy = os.path.join("/data/duckdb/_backup", os.path.basename(path))
for suffix in ("", ".wal"):
    if os.path.exists(path + suffix):
        os.remove(path + suffix)
if os.path.exists(copy):                      # backups made by this version: the database file itself
    for suffix in ("", ".wal"):
        if os.path.exists(copy + suffix):
            shutil.copy2(copy + suffix, path + suffix)
else:                                         # older backups: Parquet export
    with duckdb.connect(path) as conn:
        conn.execute("IMPORT DATABASE '"'"'/data/duckdb/_backup_export'"'"'")
with duckdb.connect(path, read_only=True) as conn:
    tables = conn.execute("select count(*) from duckdb_tables() where not internal").fetchone()[0]
    views = conn.execute("select count(*) from duckdb_views() where not internal").fetchone()[0]
print(f"DuckDB restored: {tables} tables, {views} views")
PY' < "${SRC}/data.tar.gz"
if [[ -f "${SRC}/caddy-pki.tar.gz" ]]; then
  echo "Caddy internal CA"
  "${COMPOSE[@]}" run --rm --no-deps -T caddy tar -C /data -xzf - < "${SRC}/caddy-pki.tar.gz"
  "${COMPOSE[@]}" restart caddy     # reload the restored CA (a fresh server generated its own at first start)
fi
"${COMPOSE[@]}" up -d
./scripts/wait-healthy.sh "$ENV"
echo "Restore complete."
