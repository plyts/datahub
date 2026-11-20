#!/usr/bin/env bash
# Backup: one pg_dump (custom format) per database + DuckDB file + dbt artifacts + landing files + Caddy CA.
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
# DuckDB: byte copy of the database file under a read-only connection. The read-only connection holds
# DuckDB's shared file lock, so no pipeline can write during the copy (writers need the exclusive lock).
# (EXPORT DATABASE is not used: it writes views in alphabetical order, and IMPORT then fails on a view
# that depends on a view of a later schema, e.g. marts -> staging.)
echo "DuckDB copy"
"${COMPOSE[@]}" exec -T dagster-code python - <<'PY'
import os, shutil, duckdb
src = os.environ["DUCKDB_PATH"]
out = "/data/duckdb/_backup"
shutil.rmtree(out, ignore_errors=True)
shutil.rmtree("/data/duckdb/_backup_export", ignore_errors=True)
os.makedirs(out)
with duckdb.connect(src, read_only=True):
    for suffix in ("", ".wal"):
        if os.path.exists(src + suffix):
            shutil.copy2(src + suffix, os.path.join(out, os.path.basename(src) + suffix))
PY
"${COMPOSE[@]}" exec -T dagster-code tar -C /data -czf - duckdb/_backup dbt_artifacts landing > "${DEST}/data.tar.gz"
# Caddy's internal CA (HTTPS with .internal names): keeping it avoids re-installing a new root on every workstation.
if "${COMPOSE[@]}" exec -T caddy test -d /data/caddy/pki; then
  "${COMPOSE[@]}" exec -T caddy tar -C /data -czf - caddy/pki > "${DEST}/caddy-pki.tar.gz"
fi
git rev-parse HEAD > "${DEST}/git_commit" 2>/dev/null || true
cp "envs/${ENV}.env" "${DEST}/"
find backups -mindepth 1 -maxdepth 1 -type d -mtime "+${BACKUP_RETENTION_DAYS:-14}" -exec rm -rf {} +
echo "Backup written to ${DEST} ($(du -sh "$DEST" | cut -f1))"
