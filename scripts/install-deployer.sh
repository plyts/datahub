#!/usr/bin/env bash
# Install the pull-based deployer as a systemd timer on the VM / server.
#   sudo ./scripts/install-deployer.sh staging            # follows main
#   sudo ./scripts/install-deployer.sh prod               # follows the highest vX.Y.Z tag
# Optional: GITHUB_TOKEN=github_pat_... (fine-grained, this repo only: Actions read, Deployments read/write)
#           to show deployments in GitHub and avoid the 60 requests/hour anonymous API limit.
set -euo pipefail
ENV="${1:?usage: install-deployer.sh <dev|staging|prod>}"
DEPLOYER_USER="${DEPLOYER_USER:-${SUDO_USER:-}}"
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -n "$DEPLOYER_USER" && "$DEPLOYER_USER" != root ]] || { echo "set DEPLOYER_USER (a non-root user in the docker group)" >&2; exit 1; }
[[ -d /opt/datahub/.git ]] || { echo "/opt/datahub must be a git clone of the repository" >&2; exit 1; }
id -nG "$DEPLOYER_USER" | grep -qw docker || { echo "$DEPLOYER_USER is not in the docker group" >&2; exit 1; }

# Python >= 3.9 (SLES 15: python311 package; SLES 16 / openSUSE Leap 16: python3)
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    PYTHON="$(command -v "$candidate")"; break
  fi
done
[[ -n "$PYTHON" ]] || { echo "Python >= 3.9 required (SLES 15: zypper install python311)" >&2; exit 1; }

install -d -m 0750 -o "$DEPLOYER_USER" -g docker "/var/lib/datahub-deployer/${ENV}"
install -d -m 0750 /etc/datahub
ENV_FILE="/etc/datahub/deployer-${ENV}.env"
if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  cat > "$ENV_FILE" <<CONF
DEPLOYER_PYTHON=${PYTHON}
DEPLOYER_STATE_DIR=/var/lib/datahub-deployer/${ENV}
DEPLOYER_WORKFLOW=CI
GITHUB_TOKEN=${GITHUB_TOKEN:-}
CONF
  chown "root:docker" "$ENV_FILE"; chmod 640 "$ENV_FILE"
fi
chown -R "$DEPLOYER_USER" /opt/datahub
sed "s/__DEPLOYER_USER__/${DEPLOYER_USER}/" deploy/systemd/datahub-deployer@.service > /etc/systemd/system/datahub-deployer@.service
cp deploy/systemd/datahub-deployer@.timer /etc/systemd/system/datahub-deployer@.timer
systemctl daemon-reload
systemctl enable --now "datahub-deployer@${ENV}.timer"
echo "Deployer installed for ${ENV} (user ${DEPLOYER_USER}, ${PYTHON})."
echo "  Follow it:   journalctl -u datahub-deployer@${ENV} -f"
echo "  Status:      sudo -u ${DEPLOYER_USER} ${PYTHON} /opt/datahub/scripts/deployer.py --env ${ENV} --state-dir /var/lib/datahub-deployer/${ENV} status"
echo "  Next runs:   systemctl list-timers 'datahub-deployer@*'"
