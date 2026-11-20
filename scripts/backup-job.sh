#!/usr/bin/env bash
# Nightly backup job (systemd timer datahub-backup@<env>, installed by scripts/install-backup.sh):
#   1. local backup            scripts/backup.sh <env>  -> backups/<timestamp>/
#   2. off-site copy (optional) restic, encrypted, to RESTIC_REPOSITORY (SFTP, S3, NAS...), with .env
#   3. freshness metrics        node-exporter textfile collector -> Prometheus alerts
#
#   scripts/backup-job.sh prod
#
# Settings (environment, e.g. /etc/datahub/backup-prod.env):
#   RESTIC_REPOSITORY, RESTIC_PASSWORD_FILE   off-site copy; unset repository = local backup only
#   RESTIC_KEEP_DAILY/WEEKLY/MONTHLY          off-site retention (default 7 / 4 / 12)
#   BACKUP_METRICS_DIR                        default /var/lib/datahub-backup/textfile
set -euo pipefail
cd "$(dirname "$0")/.."
ENV="${1:?usage: backup-job.sh <env>}"
METRICS_DIR="${BACKUP_METRICS_DIR:-/var/lib/datahub-backup/textfile}"
BACKUP_CMD="${BACKUP_CMD:-./scripts/backup.sh}"

# Write the metrics of one kind (local / offsite) atomically; keep the last success time on failure.
write_metrics() {  # kind status(1|0) [size_bytes]
  local kind="$1" status="$2" size="${3:-}" file now last_success
  file="${METRICS_DIR}/datahub_backup_${kind}.prom"
  now="$(date +%s)"
  last_success="$(sed -n 's/^datahub_backup_last_success_timestamp_seconds{[^}]*} //p' "$file" 2>/dev/null || true)"
  [[ "$status" == 1 ]] && last_success="$now"
  mkdir -p "$METRICS_DIR"
  {
    echo "# HELP datahub_backup_last_status 1 if the last ${kind} backup succeeded, 0 otherwise."
    echo "# TYPE datahub_backup_last_status gauge"
    echo "datahub_backup_last_status{env=\"${ENV}\",kind=\"${kind}\"} ${status}"
    echo "# TYPE datahub_backup_last_run_timestamp_seconds gauge"
    echo "datahub_backup_last_run_timestamp_seconds{env=\"${ENV}\",kind=\"${kind}\"} ${now}"
    if [[ -n "$last_success" ]]; then
      echo "# TYPE datahub_backup_last_success_timestamp_seconds gauge"
      echo "datahub_backup_last_success_timestamp_seconds{env=\"${ENV}\",kind=\"${kind}\"} ${last_success}"
    fi
    if [[ -n "$size" ]]; then
      echo "# TYPE datahub_backup_size_bytes gauge"
      echo "datahub_backup_size_bytes{env=\"${ENV}\",kind=\"${kind}\"} ${size}"
    fi
  } > "${file}.tmp"
  chmod 644 "${file}.tmp"
  mv "${file}.tmp" "$file"
}

echo "==> Local backup (${ENV})"
if ! "$BACKUP_CMD" "$ENV"; then
  write_metrics local 0
  echo "Local backup FAILED" >&2
  exit 1
fi
latest="$(find backups -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -n 1)"
[[ -n "$latest" ]] || { write_metrics local 0; echo "no backup directory found in backups/" >&2; exit 1; }
write_metrics local 1 "$(du -sb "$latest" | cut -f1)"
echo "Local backup: ${latest}"

if [[ -z "${RESTIC_REPOSITORY:-}" ]]; then
  echo "Off-site copy disabled (RESTIC_REPOSITORY not set)."
  exit 0
fi

echo "==> Off-site copy to ${RESTIC_REPOSITORY}"
restic_ok=1
if ! restic cat config >/dev/null; then
  echo "restic repository not reachable or not initialised (run 'restic init' once)" >&2
  restic_ok=0
elif ! restic backup --tag datahub --tag "$ENV" --host "datahub-${ENV}" "$latest" .env; then
  restic_ok=0
elif ! restic forget --tag datahub --host "datahub-${ENV}" --prune \
    --keep-daily "${RESTIC_KEEP_DAILY:-7}" --keep-weekly "${RESTIC_KEEP_WEEKLY:-4}" \
    --keep-monthly "${RESTIC_KEEP_MONTHLY:-12}"; then
  restic_ok=0
elif ! restic check; then
  restic_ok=0
fi
write_metrics offsite "$restic_ok"
if [[ "$restic_ok" != 1 ]]; then
  echo "Off-site copy FAILED (the local backup is fine)" >&2
  exit 1
fi
echo "Off-site copy done."
