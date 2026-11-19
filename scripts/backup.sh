#!/usr/bin/env bash
# Backup: one pg_dump (custom format) per database + DuckDB + dbt artifacts + landing files.
#   scripts/backup.sh prod            -> backups/<timestamp>/
# Keep BACKUP_RETENTION_DAYS (default 14) days of backups. Encrypt/offload them with your tooling (age, restic...).
set -euo pipefail
cd "$(dirname "$0")/.."
ENV="${1:-dev}"
COMPOSE=(docker compose --env-file "envs/${ENV}.env" --env-file .env -f docker-compose.yml -f "${COMPOSE_OVERRIDE:-compose/docker-compose.${ENV}.yml}")
DEST="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
umask 077
for db in dagster keycloak datahub grafana; do
  echo "pg_dump ${db}"
  "${COMPOSE[@]}" exec -T postgres pg_dump -U postgres -Fc "$db" > "${DEST}/${db}.dump"
done
"${COMPOSE[@]}" exec -T postgres pg_dumpall -U postgres --globals-only > "${DEST}/globals.sql"
# DuckDB: a copy is only consistent when no pipeline writes; the "duckdb" pool serializes writers,
# so the copy is taken through a read-only DuckDB EXPORT inside the code container.
echo "DuckDB export"
"${COMPOSE[@]}" exec -T dagster-code python - <<'PY'
import os, duckdb, shutil
src = os.environ["DUCKDB_PATH"]
out = "/data/duckdb/_backup_export"
shutil.rmtree(out, ignore_errors=True)
with duckdb.connect(src, read_only=True) as conn:
    conn.execute(f"EXPORT DATABASE '{out}' (FORMAT parquet)")
PY
"${COMPOSE[@]}" exec -T dagster-code tar -C /data -czf - duckdb/_backup_export dbt_artifacts landing > "${DEST}/data.tar.gz"
git rev-parse HEAD > "${DEST}/git_commit" 2>/dev/null || true
cp "envs/${ENV}.env" "${DEST}/"
find backups -mindepth 1 -maxdepth 1 -type d -mtime "+${BACKUP_RETENTION_DAYS:-14}" -exec rm -rf {} +
echo "Backup written to ${DEST} ($(du -sh "$DEST" | cut -f1))"
