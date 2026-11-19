#!/usr/bin/env bash
# Prepare a fresh SUSE Linux Enterprise Server 15 VM (VirtualBox) for the Data Hub.
# Run as root:  sudo ./scripts/bootstrap-sles.sh <admin-user>
set -euo pipefail
ADMIN_USER="${1:-$SUDO_USER}"

echo "==> System update"
zypper --non-interactive refresh
zypper --non-interactive update

echo "==> Containers module + Docker Engine + tools"
SUSEConnect -p "sle-module-containers/$(. /etc/os-release; echo "$VERSION_ID")/$(uname -m)" || true
zypper --non-interactive install docker docker-compose git make curl python3 python3-pip
systemctl enable --now docker

# Docker Compose v2 plugin (if the distribution package is older)
if ! docker compose version >/dev/null 2>&1; then
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

echo "==> Docker daemon: log rotation and live restore"
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<JSON
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

echo "==> Firewall: HTTP / HTTPS only"
if systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-service=http --add-service=https
  firewall-cmd --reload
fi

echo "==> Kernel settings"
cat > /etc/sysctl.d/90-datahub.conf <<SYSCTL
vm.max_map_count=262144
fs.inotify.max_user_watches=524288
vm.swappiness=10
SYSCTL
sysctl --system >/dev/null

echo "==> Data Hub directory"
install -d -m 0750 -o "${ADMIN_USER:-root}" /opt/datahub
echo
echo "Done. Next steps:"
echo "  git clone https://github.com/plyts/datahub /opt/datahub && cd /opt/datahub"
echo "  make secrets && make up ENV=dev"
