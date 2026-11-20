#!/usr/bin/env bash
# Install the nightly backup as a systemd timer (02:30), with an optional encrypted off-site copy.
#   sudo ./scripts/install-backup.sh prod
# Off-site copy: set RESTIC_REPOSITORY in /etc/datahub/backup-<env>.env, then run 'restic init' once
# (see docs/operations.md). The restic password is generated here: keep a copy outside the server.
set -euo pipefail
ENV="${1:?usage: install-backup.sh <dev|staging|prod>}"
BACKUP_USER="${BACKUP_USER:-${SUDO_USER:-}}"
ROOT="${INSTALL_ROOT:-}"   # tests only: prefix for /etc and /var/lib
[[ $EUID -eq 0 || -n "$ROOT" ]] || { echo "run with sudo" >&2; exit 1; }
[[ -n "$BACKUP_USER" && "$BACKUP_USER" != root ]] || { echo "set BACKUP_USER (a non-root user in the docker group)" >&2; exit 1; }
id -nG "$BACKUP_USER" | grep -qw docker || { echo "$BACKUP_USER is not in the docker group" >&2; exit 1; }
cd "$(dirname "$0")/.."

# Metrics read by node-exporter (textfile collector): world-readable, written by the backup user.
install -d -m 0755 -o "$BACKUP_USER" "${ROOT}/var/lib/datahub-backup/textfile"
install -d -m 0750 "${ROOT}/etc/datahub"

PASSWORD_FILE="/etc/datahub/restic-${ENV}.password"
if [[ ! -f "${ROOT}${PASSWORD_FILE}" ]]; then
  (umask 077; head -c 32 /dev/urandom | base64 > "${ROOT}${PASSWORD_FILE}")
  chown "root:docker" "${ROOT}${PASSWORD_FILE}"; chmod 640 "${ROOT}${PASSWORD_FILE}"
  echo "Generated ${PASSWORD_FILE}: copy it to your password manager, off-site backups are unreadable without it."
fi

ENV_FILE="/etc/datahub/backup-${ENV}.env"
if [[ ! -f "${ROOT}${ENV_FILE}" ]]; then
  (umask 077; cat > "${ROOT}${ENV_FILE}" <<CONF
# Off-site copy (restic): e.g. sftp:backup@nas.example.com:/srv/restic/datahub or s3:https://s3.example.com/datahub
# Empty = local backups only (backups/ in /opt/datahub, ${BACKUP_RETENTION_DAYS:-14} days).
RESTIC_REPOSITORY=
RESTIC_PASSWORD_FILE=${PASSWORD_FILE}
RESTIC_KEEP_DAILY=7
RESTIC_KEEP_WEEKLY=4
RESTIC_KEEP_MONTHLY=12
BACKUP_METRICS_DIR=/var/lib/datahub-backup/textfile
CONF
  )
  chown "root:docker" "${ROOT}${ENV_FILE}"; chmod 640 "${ROOT}${ENV_FILE}"
fi

mkdir -p "${ROOT}/etc/systemd/system"
sed "s/__BACKUP_USER__/${BACKUP_USER}/" deploy/systemd/datahub-backup@.service > "${ROOT}/etc/systemd/system/datahub-backup@.service"
cp deploy/systemd/datahub-backup@.timer "${ROOT}/etc/systemd/system/datahub-backup@.timer"
systemctl daemon-reload
systemctl enable --now "datahub-backup@${ENV}.timer"
echo "Nightly backup installed for ${ENV} (user ${BACKUP_USER})."
echo "  Run it now:  sudo systemctl start datahub-backup@${ENV}.service"
echo "  Follow it:   journalctl -u datahub-backup@${ENV} -f"
echo "  Next runs:   systemctl list-timers 'datahub-backup@*'"
