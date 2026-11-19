"""End-to-end tests of scripts/deployer.py: real git repositories, a fake GitHub API (CI runs,
deployments, webhook) and a real deploy command. Each test is a scenario of the CI/CD guide."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deployer", ROOT / "scripts" / "deployer.py")
deployer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployer)

# Fake deployment: fails when the checked-out commit contains a BROKEN file (= a release that does
# not start), otherwise appends the deployed commit to $DEPLOYED_LOG.
DEPLOY_CMD = "sh -c 'test ! -f BROKEN && git rev-parse HEAD >> \"$DEPLOYED_LOG\"'"


class FakeGitHub(BaseHTTPRequestHandler):
    ci: dict = {}             # sha -> list of (workflow name, status, conclusion)
    deployments: list = []    # created deployments
    statuses: list = []       # (deployment id, state, description)
    hooks: list = []          # webhook payloads
    auth: list = []

    def _send(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        FakeGitHub.auth.append(self.headers.get("Authorization"))
        if url.path.endswith("/actions/runs"):
            sha = parse_qs(url.query)["head_sha"][0]
            runs = [{"name": n, "status": s, "conclusion": c} for n, s, c in FakeGitHub.ci.get(sha, [])]
            return self._send({"workflow_runs": runs})
        return self._send({}, 404)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.path == "/hook":
            FakeGitHub.hooks.append(body["text"])
            return self._send({})
        if self.path.endswith("/deployments"):
            FakeGitHub.deployments.append(body)
            return self._send({"id": len(FakeGitHub.deployments)}, 201)
        if "/deployments/" in self.path and self.path.endswith("/statuses"):
            FakeGitHub.statuses.append((int(self.path.split("/")[-2]), body["state"], body["description"]))
            return self._send({}, 201)
        return self._send({}, 404)

    def log_message(self, *args):
        pass


@pytest.fixture
def github():
    for name in ("ci", "deployments", "statuses", "hooks", "auth"):
        setattr(FakeGitHub, name, {} if name == "ci" else [])
    server = HTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Repo:
    """origin (bare) + a developer clone that pushes + the VM clone the deployer works in."""

    def __init__(self, tmp: Path):
        self.origin, self.dev, self.vm = tmp / "origin.git", tmp / "dev", tmp / "vm"
        sh(tmp, "git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        sh(tmp, "git", "clone", "-q", str(self.origin), str(self.dev))
        for key, value in (("user.email", "dev@example.com"), ("user.name", "Dev")):
            sh(self.dev, "git", "config", key, value)
        (self.dev / "envs").mkdir()
        (self.dev / "envs" / "staging.env").write_text("DATAHUB_PUBLIC_URL=http://datahub-staging.local\n")
        self.commit("initial")
        sh(tmp, "git", "clone", "-q", str(self.origin), str(self.vm))

    def commit(self, message: str, broken: bool = False, tag: str | None = None) -> str:
        (self.dev / "CHANGELOG").write_text(message)
        broken_file = self.dev / "BROKEN"
        if broken:
            broken_file.write_text("this release does not start")
        elif broken_file.exists():
            broken_file.unlink()
        sh(self.dev, "git", "add", "-A")
        sh(self.dev, "git", "commit", "-q", "-m", message)
        if tag:
            sh(self.dev, "git", "tag", tag)
        sh(self.dev, "git", "push", "-q", "--tags", "origin", "main")
        return sh(self.dev, "git", "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


@pytest.fixture
def run(repo, github, tmp_path, monkeypatch):
    deployed_log = tmp_path / "deployed.log"
    monkeypatch.setenv("DEPLOYED_LOG", str(deployed_log))
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", f"{github}/hook")

    def _run(*command, env="staging"):
        argv = ["--env", env, "--repo-dir", str(repo.vm), "--state-dir", str(tmp_path / f"state-{env}"),
                "--github-repo", "plyts/datahub", "--api-url", github, "--deploy-cmd", DEPLOY_CMD, *command]
        code = deployer.main(argv)
        state_file = tmp_path / f"state-{env}" / "state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        deployed = deployed_log.read_text().split() if deployed_log.exists() else []
        return code, state, deployed

    return _run


def green(sha):
    FakeGitHub.ci[sha] = [("CI", "completed", "success")]


def history(tmp_path, env="staging"):
    return [json.loads(line)["action"] for line in (tmp_path / f"state-{env}" / "history.jsonl").read_text().splitlines()]


# --------------------------------------------------------------------------- nominal
def test_nominal_green_commit_is_deployed(repo, run, tmp_path):
    sha = repo.commit("feature A")
    green(sha)
    code, state, deployed = run("run")
    assert code == 0 and deployed == [sha]
    assert state["current"]["sha"] == sha and state["current"]["ref"] == "main"
    assert [s[1] for s in FakeGitHub.statuses] == ["in_progress", "success"]
    assert FakeGitHub.deployments[0]["environment"] == "staging" and FakeGitHub.deployments[0]["ref"] == sha
    assert history(tmp_path) == ["deploying", "deployed"]


def test_nominal_nothing_to_do_when_up_to_date(repo, run):
    green(repo.commit("feature A"))
    run("run")
    code, _, deployed = run("run")
    assert code == 0 and len(deployed) == 1 and len(FakeGitHub.deployments) == 1


def test_nominal_successive_releases_keep_the_previous_one(repo, run):
    a = repo.commit("A")
    green(a)
    run("run")
    b = repo.commit("B")
    green(b)
    _, state, deployed = run("run")
    assert deployed == [a, b] and state["previous"]["sha"] == a and state["current"]["sha"] == b


# --------------------------------------------------------------------------- alternative
def test_alternative_waits_while_ci_is_running(repo, run, tmp_path):
    sha = repo.commit("B")
    FakeGitHub.ci[sha] = [("CI", "in_progress", None)]
    assert run("run")[2] == []
    assert run("run")[2] == []
    assert history(tmp_path) == ["waiting_ci"], "recorded once, not every 2 minutes"
    green(sha)
    assert run("run")[2] == [sha]


def test_alternative_prod_follows_the_highest_semver_tag(repo, run):
    for message, tag in (("r1", "v1.2.0"), ("r2", "v1.10.0"), ("r3", "v1.9.9")):
        green(repo.commit(message, tag=tag))
    repo.commit("work in progress on main")  # never deployed to prod: not tagged
    code, state, _ = run("run", env="prod")
    assert code == 0 and state["current"]["ref"] == "v1.10.0"


def test_alternative_manual_rollback_to_a_tag(repo, run):
    v1 = repo.commit("r1", tag="v1.0.0")
    green(v1)
    green(repo.commit("r2", tag="v1.1.0"))
    run("run", env="prod")
    code, state, _ = run("rollback", "--to", "v1.0.0", env="prod")
    assert code == 0 and state["current"] == {**state["current"], "sha": v1, "ref": "v1.0.0"}


def test_alternative_works_without_github_token(repo, run, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    sha = repo.commit("A")
    green(sha)
    assert run("run")[2] == [sha]
    assert FakeGitHub.deployments == [] and set(FakeGitHub.auth) == {None}


# --------------------------------------------------------------------------- exception
def test_exception_ci_missing_is_treated_as_waiting(repo, run, tmp_path):
    repo.commit("pushed, CI not started yet")
    assert run("run")[2] == []
    assert history(tmp_path) == ["waiting_ci"]


def test_exception_concurrent_runs_are_skipped(repo, run, tmp_path):
    green(repo.commit("A"))
    state_dir = tmp_path / "state-staging"
    with deployer.single_instance(state_dir) as acquired:
        assert acquired
        code, _, deployed = run("run")
    assert code == 0 and deployed == []


def test_exception_github_unreachable_is_logged_once(repo, tmp_path, monkeypatch):
    repo.commit("A")
    argv = ["--env", "staging", "--repo-dir", str(repo.vm), "--state-dir", str(tmp_path / "s"),
            "--github-repo", "plyts/datahub", "--api-url", "http://127.0.0.1:1", "--deploy-cmd", DEPLOY_CMD, "run"]
    assert deployer.main(argv) == 3
    assert deployer.main(argv) == 3
    events = (tmp_path / "s" / "history.jsonl").read_text().splitlines()
    assert len(events) == 1 and json.loads(events[0])["action"] == "error"


# --------------------------------------------------------------------------- error
def test_error_red_ci_blocks_the_deployment(repo, run, tmp_path):
    a = repo.commit("A")
    green(a)
    run("run")
    b = repo.commit("B with a failing test")
    FakeGitHub.ci[b] = [("CI", "completed", "failure")]
    code, state, deployed = run("run")
    run("run")
    assert code == 0 and deployed == [a] and state["current"]["sha"] == a
    assert b in state["blocked"]
    assert history(tmp_path)[-1] == "blocked" and history(tmp_path).count("blocked") == 1
    assert len(FakeGitHub.hooks) == 1 and "CI est en échec" in FakeGitHub.hooks[0]


def test_error_failed_deployment_rolls_back_automatically(repo, run, tmp_path):
    a = repo.commit("A")
    green(a)
    run("run")
    broken = repo.commit("B does not start", broken=True)
    green(broken)
    code, state, deployed = run("run")
    assert code == 1
    assert deployed == [a, a], "A deployed, B failed, A redeployed by the rollback"
    assert state["current"]["sha"] == a and broken in state["failed"]
    assert sh(repo.vm, "git", "rev-parse", "HEAD") == a
    assert [s[1] for s in FakeGitHub.statuses] == ["in_progress", "success", "in_progress", "failure",
                                                   "in_progress", "success"]
    assert history(tmp_path)[-3:] == ["deploying", "deploy_failed", "rolled_back"]
    assert "retour automatique" in FakeGitHub.hooks[-1]
    # The broken commit is not retried every 2 minutes...
    assert run("run")[2] == [a, a]
    # ...but the fix is deployed as soon as it is pushed and green.
    fix = repo.commit("C fixes B")
    green(fix)
    assert run("run")[2] == [a, a, fix]


def test_error_first_deployment_fails_without_previous_version(repo, run):
    green(repo.commit("broken from day one", broken=True))
    code, state, deployed = run("run")
    assert code == 1 and deployed == [] and state["current"] is None
    assert "aucune version précédente" in FakeGitHub.hooks[-1]


def test_error_rollback_failure_requires_manual_intervention(repo, run, tmp_path, monkeypatch):
    a = repo.commit("A")
    green(a)
    run("run")
    green(repo.commit("B", broken=True))
    monkeypatch.setenv("DEPLOYED_LOG", "/nonexistent-dir/deployed.log")  # every deployment now fails
    code, state, _ = run("run")
    assert code == 2 and state["current"]["sha"] == a
    assert history(tmp_path)[-1] == "rollback_failed"
    assert "CRITIQUE" in FakeGitHub.hooks[-1]


def test_manual_deploy_refuses_a_commit_without_green_ci(repo, run):
    sha = repo.commit("A")
    FakeGitHub.ci[sha] = [("CI", "completed", "failure")]
    assert run("deploy", "--ref", sha)[0] == 3
    assert run("deploy", "--ref", sha, "--skip-ci")[2] == [sha]


def test_status_command(repo, run, capsys):
    green(repo.commit("A"))
    run("run")
    capsys.readouterr()
    assert run("status")[0] == 0
    out = capsys.readouterr().out
    assert '"ref": "main"' in out and "deployed" in out


def test_missing_repository_is_a_configuration_error(tmp_path, capsys):
    """Exit 3 (unit failure in systemd), never a traceback with exit 1 (which systemd treats as success)."""
    code = deployer.main(["--env", "staging", "--repo-dir", str(tmp_path / "absent"),
                          "--state-dir", str(tmp_path / "state"), "run"])
    assert code == 3
