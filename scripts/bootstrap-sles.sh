#!/usr/bin/env bash
# Prepare a fresh SUSE VM for the Data Hub: SLES 16, SLES 15 SP7 or openSUSE Leap 16.
# Run as root:  sudo ./scripts/bootstrap-sles.sh <admin-user>
set -euo pipefail
ADMIN_USER="${1:-${SUDO_USER:-}}"
DATAHUB_DIR="${DATAHUB_DIR:-/opt/datahub}"
ROOT="${BOOTSTRAP_ROOT:-}"   # tests only: prefix for the files written under /etc and /usr
# shellcheck source=/dev/null
. "${ROOT}/etc/os-release"
echo "==> ${PRETTY_NAME}"

echo "==> System update"
zypper --non-interactive refresh
zypper --non-interactive update

echo "==> Docker Engine + tools"
if [[ "${ID}" == "sles" && "${VERSION_ID}" == 15* ]]; then
  # SLES 15: Docker lives in the Containers module.
  SUSEConnect -p "sle-module-containers/${VERSION_ID}/$(uname -m)" || true
fi
zypper --non-interactive install git make curl python3 python3-pip
# Package name differs between releases: "docker" (Leap, SLES 15) or "docker-stable".
zypper --non-interactive install docker || zypper --non-interactive install docker-stable
zypper --non-interactive install docker-compose || echo "docker-compose package not available: plugin downloaded below"
systemctl enable --now docker

# Docker Compose v2 must answer to "docker compose".
if ! docker compose version >/dev/null 2>&1; then
  plugins="${ROOT}/usr/local/lib/docker/cli-plugins"
  mkdir -p "${plugins}"
  if [[ -x "${ROOT}/usr/bin/docker-compose" ]]; then
    ln -sf /usr/bin/docker-compose "${plugins}/docker-compose"
  else
    curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
      -o "${plugins}/docker-compose"
    chmod +x "${plugins}/docker-compose"
  fi
fi
docker compose version

echo "==> Docker daemon: log rotation and live restore"
mkdir -p "${ROOT}/etc/docker"
cat > "${ROOT}/etc/docker/daemon.json" <<JSON
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "5"},
  "live-restore": true
}
JSON
systemctl restart docker

if [[ -n "${ADMIN_USER}" ]]; then
  usermod -aG docker "${ADMIN_USER}"
  echo "User ${ADMIN_USER} added to the docker group (log out / in)."
fi

echo "==> Firewall: SSH, HTTP, HTTPS only"
if systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-service=ssh --add-service=http --add-service=https
  firewall-cmd --reload
fi

echo "==> Kernel settings"
mkdir -p "${ROOT}/etc/sysctl.d"
cat > "${ROOT}/etc/sysctl.d/90-datahub.conf" <<SYSCTL
vm.max_map_count=262144
fs.inotify.max_user_watches=524288
vm.swappiness=10
SYSCTL
sysctl --system >/dev/null

echo "==> Data Hub directory"
install -d -m 0750 -o "${ADMIN_USER:-root}" "${DATAHUB_DIR}"

# SLES 16 and Leap 16 enforce SELinux: containers may only read bind-mounted files labelled
# container_file_t (Caddyfile, Prometheus rules, Grafana provisioning...). The rule is persistent;
# files cloned later inherit the label from the directory.
if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
  echo "==> SELinux enforcing: labelling ${DATAHUB_DIR} for containers"
  command -v semanage >/dev/null 2>&1 || zypper --non-interactive install policycoreutils-python-utils
  semanage fcontext -a -t container_file_t "${DATAHUB_DIR}(/.*)?" 2>/dev/null \
    || semanage fcontext -m -t container_file_t "${DATAHUB_DIR}(/.*)?"
  restorecon -R "${DATAHUB_DIR}"
fi

echo
echo "Done. Next steps (as ${ADMIN_USER:-root}, after logging out and in again):"
echo "  git clone https://github.com/plyts/datahub ${DATAHUB_DIR} && cd ${DATAHUB_DIR}"
echo "  make secrets && make up ENV=dev"
