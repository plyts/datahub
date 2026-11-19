"""scripts/bootstrap-sles.sh on the three supported SUSE releases, with every system command stubbed.

Each stub records its arguments, so the tests check what the script would really do on
SLES 16, SLES 15 SP7 and openSUSE Leap 16 (package fallbacks, Compose plugin, SELinux labels).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap-sles.sh"

OS_RELEASE = {
    "leap16": 'ID="opensuse-leap"\nVERSION_ID="16.0"\nPRETTY_NAME="openSUSE Leap 16.0"\n',
    "sles16": 'ID="sles"\nVERSION_ID="16.0"\nPRETTY_NAME="SUSE Linux Enterprise Server 16.0"\n',
    "sles15": 'ID="sles"\nVERSION_ID="15.7"\nPRETTY_NAME="SUSE Linux Enterprise Server 15 SP7"\n',
}

STUB = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
case "$(basename "$0")${*:+ $*}" in
  "zypper --non-interactive install docker")         [[ "$HAS_DOCKER_PKG" == 1 ]] || exit 104 ;;
  "zypper --non-interactive install docker-compose") [[ "$HAS_COMPOSE_PKG" == 1 ]] || exit 104 ;;
  "docker compose version")
    plugin="$BOOTSTRAP_ROOT/usr/local/lib/docker/cli-plugins/docker-compose"
    [[ "$COMPOSE_BUILTIN" == 1 || -e "$plugin" || -L "$plugin" ]] || exit 1 ;;
  "getenforce") echo "$SELINUX" ;;
  "semanage fcontext -a"*) [[ "$FCONTEXT_EXISTS" != 1 ]] || exit 1 ;;
  "curl "*) out="${*: -1}"; echo fake-compose > "$out" ;;
  "systemctl is-active --quiet firewalld") exit 0 ;;
esac
exit 0
"""

COMMANDS = [
    "zypper",
    "SUSEConnect",
    "systemctl",
    "docker",
    "usermod",
    "firewall-cmd",
    "sysctl",
    "install",
    "getenforce",
    "semanage",
    "restorecon",
    "curl",
]


def run_bootstrap(tmp_path: Path, release: str, **flags) -> list[str]:
    root, bin_dir = tmp_path / "root", tmp_path / "bin"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text(OS_RELEASE[release])
    bin_dir.mkdir()
    for name in COMMANDS:
        stub = bin_dir / name
        stub.write_text(STUB)
        stub.chmod(0o755)
    log = tmp_path / "calls.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "BOOTSTRAP_ROOT": str(root),
        "DATAHUB_DIR": str(tmp_path / "opt-datahub"),
        "HAS_DOCKER_PKG": "1",
        "HAS_COMPOSE_PKG": "1",
        "COMPOSE_BUILTIN": "1",
        "SELINUX": "Enforcing",
        "FCONTEXT_EXISTS": "0",
        **{k: str(v) for k, v in flags.items()},
    }
    result = subprocess.run(["bash", str(SCRIPT), "admin"], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return log.read_text().splitlines()


def test_leap16_with_selinux_labels_the_datahub_directory(tmp_path):
    calls = run_bootstrap(tmp_path, "leap16")
    target = f"{tmp_path / 'opt-datahub'}(/.*)?"
    assert f"semanage fcontext -a -t container_file_t {target}" in calls
    assert f"restorecon -R {tmp_path / 'opt-datahub'}" in calls
    assert not any(c.startswith("SUSEConnect") for c in calls), "no module registration outside SLES 15"
    assert "zypper --non-interactive install docker" in calls
    assert "firewall-cmd --permanent --add-service=ssh --add-service=http --add-service=https" in calls
    assert "usermod -aG docker admin" in calls


def test_rerun_updates_an_existing_selinux_rule(tmp_path):
    calls = run_bootstrap(tmp_path, "leap16", FCONTEXT_EXISTS=1)
    assert any(c.startswith("semanage fcontext -m -t container_file_t") for c in calls)


def test_sles16_falls_back_to_docker_stable_and_downloads_compose(tmp_path):
    calls = run_bootstrap(tmp_path, "sles16", HAS_DOCKER_PKG=0, HAS_COMPOSE_PKG=0, COMPOSE_BUILTIN=0)
    assert calls.index("zypper --non-interactive install docker") < calls.index(
        "zypper --non-interactive install docker-stable"
    )
    assert any(c.startswith("curl -fsSL https://github.com/docker/compose/releases/latest/download/") for c in calls)
    plugin = tmp_path / "root/usr/local/lib/docker/cli-plugins/docker-compose"
    assert plugin.exists() and os.access(plugin, os.X_OK)
    assert calls[-1].startswith("restorecon"), "SELinux labelling is the last step"


def test_compose_package_without_plugin_is_linked_not_downloaded(tmp_path):
    root_bin = tmp_path / "root/usr/bin"
    root_bin.mkdir(parents=True)
    (root_bin / "docker-compose").write_text("#!/bin/sh\n")
    (root_bin / "docker-compose").chmod(0o755)
    calls = run_bootstrap(tmp_path, "leap16", COMPOSE_BUILTIN=0)
    plugin = tmp_path / "root/usr/local/lib/docker/cli-plugins/docker-compose"
    assert plugin.is_symlink() and os.readlink(plugin) == "/usr/bin/docker-compose"
    assert not any(c.startswith("curl") for c in calls)


def test_sles15_registers_the_containers_module_and_skips_selinux_when_disabled(tmp_path):
    calls = run_bootstrap(tmp_path, "sles15", SELINUX="Disabled")
    assert f"SUSEConnect -p sle-module-containers/15.7/{os.uname().machine}" in calls
    assert not any(c.startswith(("semanage", "restorecon")) for c in calls)


@pytest.mark.parametrize("release", sorted(OS_RELEASE))
def test_daemon_and_kernel_settings_are_written(tmp_path, release):
    run_bootstrap(tmp_path, release)
    daemon = json.loads((tmp_path / "root/etc/docker/daemon.json").read_text())
    assert daemon["log-opts"] == {"max-size": "10m", "max-file": "5"} and daemon["live-restore"] is True
    assert "vm.max_map_count=262144" in (tmp_path / "root/etc/sysctl.d/90-datahub.conf").read_text()
