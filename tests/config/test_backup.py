"""Nightly backup: scripts/backup-job.sh (local + restic off-site + metrics) and its systemd installation.

The local backup command and restic are stubs that record their arguments; everything else is real.
"""

import configparser
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
JOB = ROOT / "scripts" / "backup-job.sh"
INSTALLER = ROOT / "scripts" / "install-backup.sh"

FAKE_BACKUP = """#!/usr/bin/env bash
[[ "${BACKUP_FAILS:-0}" == 1 ]] && { echo "pg_dump: connection refused" >&2; exit 1; }
mkdir -p "backups/2026112${RUN:-0}-023000" && echo dump > "backups/2026112${RUN:-0}-023000/datahub.dump"
"""

FAKE_RESTIC = """#!/usr/bin/env bash
echo "restic $*" >> "$CALLS"
[[ "$1" == cat && "${RESTIC_NOT_INIT:-0}" == 1 ]] && exit 1
[[ "$1" == backup && "${RESTIC_BACKUP_FAILS:-0}" == 1 ]] && exit 1
exit 0
"""


def make_executable(path: Path, content: str) -> Path:
    path.write_text(content)
    path.chmod(0o755)
    return path


def workdir(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(JOB, repo / "scripts" / "backup-job.sh")
    (repo / ".env").write_text("SECRET=1\n")
    make_executable(tmp_path / "fake-backup.sh", FAKE_BACKUP)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_executable(bin_dir / "restic", FAKE_RESTIC)
    return repo


def run_job(tmp_path: Path, **env):
    repo = tmp_path / "repo"
    full_env = {
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "BACKUP_CMD": str(tmp_path / "fake-backup.sh"),
        "BACKUP_METRICS_DIR": str(tmp_path / "metrics"),
        "CALLS": str(tmp_path / "restic.calls"),
        **{k: str(v) for k, v in env.items()},
    }
    return subprocess.run(["bash", str(repo / "scripts" / "backup-job.sh"), "prod"], cwd=repo, env=full_env,
                          capture_output=True, text=True, check=False)


def metrics(tmp_path: Path, kind: str) -> dict:
    text = (tmp_path / "metrics" / f"datahub_backup_{kind}.prom").read_text()
    values = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        match = re.fullmatch(r'(\w+)\{env="prod",kind="' + kind + r'"\} (\d+)', line)
        assert match, f"unexpected metric line: {line}"
        values[match[1]] = int(match[2])
    return values


def restic_calls(tmp_path: Path) -> list[str]:
    calls = tmp_path / "restic.calls"
    return calls.read_text().splitlines() if calls.exists() else []


def test_local_backup_only_writes_fresh_metrics(tmp_path):
    workdir(tmp_path)
    result = run_job(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Off-site copy disabled" in result.stdout
    local = metrics(tmp_path, "local")
    assert local["datahub_backup_last_status"] == 1
    assert local["datahub_backup_last_success_timestamp_seconds"] == local["datahub_backup_last_run_timestamp_seconds"]
    assert local["datahub_backup_size_bytes"] > 0
    assert not (tmp_path / "metrics" / "datahub_backup_offsite.prom").exists()
    assert restic_calls(tmp_path) == []


def test_failed_local_backup_keeps_the_last_success_time(tmp_path):
    workdir(tmp_path)
    assert run_job(tmp_path).returncode == 0
    first_success = metrics(tmp_path, "local")["datahub_backup_last_success_timestamp_seconds"]
    result = run_job(tmp_path, BACKUP_FAILS=1)
    assert result.returncode == 1
    local = metrics(tmp_path, "local")
    assert local["datahub_backup_last_status"] == 0
    assert local["datahub_backup_last_success_timestamp_seconds"] == first_success


def test_offsite_copy_sends_the_newest_backup_and_the_secrets_then_prunes(tmp_path):
    workdir(tmp_path)
    run_job(tmp_path, RUN=1)
    result = run_job(tmp_path, RUN=2, RESTIC_REPOSITORY="sftp:nas:/srv/restic", RESTIC_KEEP_DAILY=10)
    assert result.returncode == 0, result.stderr
    assert restic_calls(tmp_path) == [
        "restic cat config",
        "restic backup --tag datahub --tag prod --host datahub-prod backups/20261122-023000 .env",
        "restic forget --tag datahub --host datahub-prod --prune --keep-daily 10 --keep-weekly 4 --keep-monthly 12",
        "restic check",
    ]
    assert metrics(tmp_path, "offsite")["datahub_backup_last_status"] == 1


def test_uninitialised_repository_fails_the_offsite_copy_only(tmp_path):
    workdir(tmp_path)
    result = run_job(tmp_path, RESTIC_REPOSITORY="sftp:nas:/srv/restic", RESTIC_NOT_INIT=1)
    assert result.returncode == 1
    assert "restic init" in result.stderr
    assert restic_calls(tmp_path) == ["restic cat config"]
    assert metrics(tmp_path, "local")["datahub_backup_last_status"] == 1
    offsite = metrics(tmp_path, "offsite")
    assert offsite["datahub_backup_last_status"] == 0
    assert "datahub_backup_last_success_timestamp_seconds" not in offsite


def test_failed_restic_backup_skips_the_pruning(tmp_path):
    workdir(tmp_path)
    result = run_job(tmp_path, RESTIC_REPOSITORY="s3:https://s3.example.com/datahub", RESTIC_BACKUP_FAILS=1)
    assert result.returncode == 1
    assert [c.split()[1] for c in restic_calls(tmp_path)] == ["cat", "backup"]


# ------------------------------------------------------------------ installation (systemd)

STUB = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
case "$(basename "$0")" in
  id) echo "users docker" ;;
  install) mkdir -p "${@: -1}" ;;
esac
exit 0
"""


def run_installer(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ["id", "install", "chown", "systemctl"]:
        make_executable(bin_dir / name, STUB)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "INSTALL_ROOT": str(tmp_path / "root"),
           "BACKUP_USER": "admin", "CALLS": str(tmp_path / "calls")}
    return subprocess.run(["bash", str(INSTALLER), "prod"], env=env, capture_output=True, text=True, check=False)


def test_installer_writes_settings_password_units_and_enables_the_timer(tmp_path):
    result = run_installer(tmp_path)
    assert result.returncode == 0, result.stderr
    root = tmp_path / "root"
    settings = (root / "etc/datahub/backup-prod.env").read_text()
    assert "RESTIC_REPOSITORY=\n" in settings
    assert "RESTIC_PASSWORD_FILE=/etc/datahub/restic-prod.password" in settings
    password = (root / "etc/datahub/restic-prod.password").read_text().strip()
    assert len(password) >= 40
    assert oct((root / "etc/datahub/restic-prod.password").stat().st_mode)[-3:] == "640"
    service = (root / "etc/systemd/system/datahub-backup@.service").read_text()
    assert "User=admin" in service and "__BACKUP_USER__" not in service
    assert (root / "var/lib/datahub-backup/textfile").is_dir()
    calls = (tmp_path / "calls").read_text().splitlines()
    assert "systemctl enable --now datahub-backup@prod.timer" in calls

    # A second run keeps the existing password and settings (never regenerates the off-site key).
    (root / "etc/datahub/backup-prod.env").write_text(settings.replace("RESTIC_REPOSITORY=", "RESTIC_REPOSITORY=sftp:x"))
    assert run_installer(tmp_path).returncode == 0
    assert (root / "etc/datahub/restic-prod.password").read_text().strip() == password
    assert "RESTIC_REPOSITORY=sftp:x" in (root / "etc/datahub/backup-prod.env").read_text()


def unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(ROOT / "deploy" / "systemd" / name)
    return parser


def test_backup_units_run_the_job_nightly_and_catch_up_missed_runs():
    service, timer = unit("datahub-backup@.service"), unit("datahub-backup@.timer")
    assert service["Service"]["ExecStart"] == "/opt/datahub/scripts/backup-job.sh %i"
    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["Group"] == "docker"
    assert timer["Timer"]["OnCalendar"] == "*-*-* 02:30:00"
    assert timer["Timer"]["Persistent"] == "true"


def test_node_exporter_reads_the_backup_metrics():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    command = compose["services"]["node-exporter"]["command"]
    assert "--collector.textfile.directory=/host/var/lib/datahub-backup/textfile" in command
    assert "BACKUP_METRICS_DIR=/var/lib/datahub-backup/textfile" in INSTALLER.read_text()
