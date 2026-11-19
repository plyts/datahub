#!/usr/bin/env python3
"""Data Hub — pull-based continuous deployment agent (GitOps).

Runs on the VM / server itself, every 2 minutes (systemd timer `datahub-deployer@<env>.timer`):

  1. `git fetch` the repository (outbound HTTPS only: no inbound port, no CI runner on the machine);
  2. pick the target commit: the tip of `main` (dev / staging) or the highest `vX.Y.Z` tag (prod);
  3. deploy it ONLY if the GitHub Actions workflow "CI" succeeded on that exact commit;
  4. run `scripts/deploy.sh <env>` (backup, build, start, healthchecks, smoke tests);
  5. if the deployment fails, roll back automatically to the last commit that deployed successfully;
  6. report each step as a GitHub *deployment status* (optional token), in `history.jsonl`,
     in the journal (journalctl) and to the notification webhook.

Why pull and not a self-hosted GitHub runner: the repository is public, and GitHub advises against
self-hosted runners on public repositories (a pull request could run code on the machine).

Usage:
  deployer.py --env staging run             # one cycle (what the timer does)
  deployer.py --env staging status          # current / previous deployment + last events
  deployer.py --env prod deploy --ref v1.3.0 [--skip-ci]   # manual deployment
  deployer.py --env prod rollback --to v1.2.0               # manual rollback

Exit codes: 0 = nothing to do / deployed / waiting / blocked · 1 = deployment failed and rolled back
(or nothing to roll back to) · 2 = rollback failed too (manual intervention) · 3 = usage / configuration.
Standard library only, Python >= 3.9.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("datahub-deployer")

SEMVER_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
DEFAULT_TRACK = {"dev": "main", "desktop": "main", "staging": "main", "prod": "tags"}


class DeployerError(RuntimeError):
    pass


# --------------------------------------------------------------------------- helpers
def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short(sha: str | None) -> str:
    return (sha or "-")[:8]


def git(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise DeployerError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def github_repo_from_origin(repo_dir: Path) -> str:
    url = git(repo_dir, "remote", "get-url", "origin")
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        raise DeployerError(f"cannot infer owner/repo from origin URL {url!r}; use --github-repo")
    return match.group(1)


# --------------------------------------------------------------------------- GitHub API
class GitHub:
    def __init__(self, api_url: str, repo: str, token: str | None, timeout: float = 20.0):
        self.api_url = api_url.rstrip("/")
        self.repo = repo
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.api_url}/repos/{self.repo}{path}", data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "datahub-deployer")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - fixed https API
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise DeployerError(f"GitHub API {method} {path}: HTTP {exc.code} {exc.read()[:200]!r}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DeployerError(f"GitHub API unreachable: {exc}") from exc

    def ci_conclusion(self, sha: str, workflow: str) -> str:
        """'success' | 'pending' | 'failure' | 'missing' for the given workflow on that exact commit."""
        runs = self._request("GET", f"/actions/runs?head_sha={sha}&per_page=50").get("workflow_runs", [])
        runs = [r for r in runs if r.get("name") == workflow]
        if not runs:
            return "missing"
        if any(r.get("conclusion") == "success" for r in runs):
            return "success"
        if any(r.get("status") != "completed" for r in runs):
            return "pending"
        return "failure"

    def create_deployment(self, sha: str, env: str, description: str) -> int | None:
        if not self.token:
            return None
        body = {"ref": sha, "environment": env, "auto_merge": False, "required_contexts": [],
                "description": description[:140], "production_environment": env == "prod"}
        return self._request("POST", "/deployments", body).get("id")

    def set_status(self, deployment_id: int | None, state: str, description: str, url: str | None = None) -> None:
        if deployment_id is None:
            return
        body = {"state": state, "description": description[:140]}
        if url:
            body["environment_url"] = url
        self._request("POST", f"/deployments/{deployment_id}/statuses", body)


# --------------------------------------------------------------------------- deployer
class Deployer:
    def __init__(self, args: argparse.Namespace):
        self.env = args.env
        self.repo_dir = Path(args.repo_dir).resolve()
        self.state_dir = Path(args.state_dir or f"/var/lib/datahub-deployer/{self.env}")
        self.track = args.track or DEFAULT_TRACK.get(self.env, "main")
        self.workflow = args.workflow
        self.deploy_cmd = args.deploy_cmd
        self.timeout = args.timeout
        self.dry_run = args.dry_run
        self.public_url = args.public_url or self._public_url_from_env_file()
        self.webhook = os.environ.get("NOTIFY_WEBHOOK_URL", "")
        repo = args.github_repo or os.environ.get("DEPLOYER_GITHUB_REPO") or github_repo_from_origin(self.repo_dir)
        self.github = GitHub(args.api_url, repo, os.environ.get("GITHUB_TOKEN") or None)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "logs").mkdir(exist_ok=True)

    # ------------------------------------------------------------- state
    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    def load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {"current": None, "previous": None, "failed": [], "blocked": []}

    def save_state(self, state: dict) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.state_file)

    def record(self, action: str, sha: str | None, ref: str | None, detail: str = "") -> None:
        event = {"ts": now(), "env": self.env, "action": action, "sha": sha, "ref": ref, "detail": detail}
        with (self.state_dir / "history.jsonl").open("a") as fh:
            fh.write(json.dumps(event) + "\n")
        log.info("%s %s %s %s", action.upper(), short(sha), ref or "", detail)

    def last_event(self) -> dict | None:
        path = self.state_dir / "history.jsonl"
        if not path.exists():
            return None
        lines = path.read_text().strip().splitlines()
        return json.loads(lines[-1]) if lines else None

    def record_once(self, action: str, sha: str | None, ref: str | None, detail: str = "") -> bool:
        """Record an event unless it is the same as the last one (the timer runs every 2 minutes)."""
        last = self.last_event()
        if last and last.get("action") == action and last.get("sha") == sha:
            return False
        self.record(action, sha, ref, detail)
        return True

    def notify(self, text: str) -> None:
        if not self.webhook:
            return
        request = urllib.request.Request(self.webhook, data=json.dumps({"text": text}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=10).close()  # noqa: S310 - URL configured by the admin
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("notification failed: %s", exc)

    def _public_url_from_env_file(self) -> str | None:
        env_file = self.repo_dir / "envs" / f"{self.env}.env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("DATAHUB_PUBLIC_URL="):
                    return line.split("=", 1)[1].strip()
        return None

    # ------------------------------------------------------------- git
    def fetch(self) -> None:
        git(self.repo_dir, "fetch", "--prune", "--tags", "--force", "--quiet", "origin")

    def resolve_target(self) -> tuple[str, str]:
        if self.track == "main":
            return "main", git(self.repo_dir, "rev-parse", "origin/main^{commit}")
        tags = [t for t in git(self.repo_dir, "tag", "--list", "v*").splitlines() if SEMVER_TAG.match(t)]
        if not tags:
            raise DeployerError("no vX.Y.Z tag to deploy")
        best = max(tags, key=lambda t: tuple(int(x) for x in SEMVER_TAG.match(t).groups()))
        return best, git(self.repo_dir, "rev-list", "-n", "1", best)

    def resolve_ref(self, ref: str) -> str:
        return git(self.repo_dir, "rev-parse", f"{ref}^{{commit}}")

    # ------------------------------------------------------------- deployment
    def run_deploy(self, sha: str, ref: str) -> bool:
        git(self.repo_dir, "-c", "advice.detachedHead=false", "checkout", "--force", "--quiet", "--detach", sha)
        command = shlex.split(self.deploy_cmd.format(env=self.env, sha=sha, ref=ref))
        logfile = self.state_dir / "logs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{short(sha)}.log"
        log.info("running %s (log: %s)", " ".join(command), logfile)
        with logfile.open("w") as out:
            try:
                result = subprocess.run(command, cwd=self.repo_dir, stdout=out, stderr=subprocess.STDOUT,
                                        timeout=self.timeout, check=False)
            except subprocess.TimeoutExpired:
                out.write(f"\nTIMEOUT after {self.timeout}s\n")
                return False
        return result.returncode == 0

    def deploy(self, ref: str, sha: str, state: dict, reason: str) -> int:
        current = state.get("current")
        deployment_id = self.github.create_deployment(sha, self.env, f"{reason}: {ref} {short(sha)}")
        self.github.set_status(deployment_id, "in_progress", f"Deploying {ref} {short(sha)}")
        self.record("deploying", sha, ref, reason)
        if self.run_deploy(sha, ref):
            state["previous"], state["current"] = current, {"sha": sha, "ref": ref, "deployed_at": now()}
            state["failed"] = [s for s in state.get("failed", []) if s != sha]
            self.save_state(state)
            self.github.set_status(deployment_id, "success", f"{ref} {short(sha)} deployed", self.public_url)
            self.record("deployed", sha, ref, reason)
            return 0

        self.github.set_status(deployment_id, "failure", f"Deployment of {ref} {short(sha)} failed")
        state.setdefault("failed", []).append(sha)
        self.save_state(state)
        self.record("deploy_failed", sha, ref, "see logs/ for the deploy.sh output")
        if not current:
            self.notify(f"[Data Hub {self.env.upper()}] Échec du déploiement de {ref} ({short(sha)}), "
                        "aucune version précédente vers laquelle revenir.")
            return 1

        rollback_id = self.github.create_deployment(current["sha"], self.env, f"Automatic rollback after {short(sha)}")
        self.github.set_status(rollback_id, "in_progress", f"Rolling back to {current['ref']}")
        if self.run_deploy(current["sha"], current["ref"]):
            self.github.set_status(rollback_id, "success", f"Rolled back to {current['ref']} {short(current['sha'])}",
                                   self.public_url)
            self.record("rolled_back", current["sha"], current["ref"], f"after failure of {short(sha)}")
            self.notify(f"[Data Hub {self.env.upper()}] Échec du déploiement de {ref} ({short(sha)}) : "
                        f"retour automatique à {current['ref']} ({short(current['sha'])}).")
            return 1
        self.github.set_status(rollback_id, "failure", "Rollback failed: manual intervention required")
        self.record("rollback_failed", current["sha"], current["ref"], "MANUAL INTERVENTION REQUIRED")
        self.notify(f"[Data Hub {self.env.upper()}] CRITIQUE : échec du déploiement de {short(sha)} ET du retour "
                    f"à {short(current['sha'])}. Intervention manuelle nécessaire.")
        return 2

    # ------------------------------------------------------------- commands
    def cycle(self) -> int:
        self.fetch()
        ref, sha = self.resolve_target()
        state = self.load_state()
        current = (state.get("current") or {}).get("sha")
        if sha == current:
            log.info("up to date: %s %s", ref, short(sha))
            return 0
        if sha in state.get("failed", []):
            self.record_once("skipped_failed", sha, ref, "already failed on this commit, waiting for a new one")
            return 0
        ci = self.github.ci_conclusion(sha, self.workflow)
        if ci in ("pending", "missing"):
            self.record_once("waiting_ci", sha, ref, f"workflow {self.workflow!r} is {ci}")
            return 0
        if ci == "failure":
            if self.record_once("blocked", sha, ref, f"workflow {self.workflow!r} failed on this commit"):
                state.setdefault("blocked", []).append(sha)
                self.save_state(state)
                self.notify(f"[Data Hub {self.env.upper()}] {ref} ({short(sha)}) non déployé : la CI est en échec.")
            return 0
        if self.dry_run:
            log.info("dry run: would deploy %s %s", ref, short(sha))
            return 0
        return self.deploy(ref, sha, state, "automatic")

    def manual(self, ref: str, skip_ci: bool, reason: str) -> int:
        self.fetch()
        sha = self.resolve_ref(ref)
        if not skip_ci:
            ci = self.github.ci_conclusion(sha, self.workflow)
            if ci != "success":
                log.error("CI is %s on %s %s: refusing (use --skip-ci to force)", ci, ref, short(sha))
                return 3
        return self.deploy(ref, sha, self.load_state(), reason)

    def status(self) -> int:
        state = self.load_state()
        print(json.dumps({k: state.get(k) for k in ("current", "previous", "failed", "blocked")}, indent=2))
        path = self.state_dir / "history.jsonl"
        if path.exists():
            print("\nLast events:")
            for line in path.read_text().strip().splitlines()[-10:]:
                event = json.loads(line)
                print(f"  {event['ts']}  {event['action']:<16} {short(event['sha'])}  {event['ref'] or ''}  "
                      f"{event['detail']}")
        return 0


@contextmanager
def single_instance(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "lock").open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Data Hub pull-based deployer")
    parser.add_argument("--env", required=True, choices=["dev", "desktop", "staging", "prod"])
    parser.add_argument("--repo-dir", default=os.environ.get("DEPLOYER_REPO_DIR", "/opt/datahub"))
    parser.add_argument("--state-dir", default=os.environ.get("DEPLOYER_STATE_DIR"))
    parser.add_argument("--track", choices=["main", "tags"], default=os.environ.get("DEPLOYER_TRACK"))
    parser.add_argument("--workflow", default=os.environ.get("DEPLOYER_WORKFLOW", "CI"))
    parser.add_argument("--github-repo", default=None)
    parser.add_argument("--api-url", default=os.environ.get("DEPLOYER_API_URL", "https://api.github.com"))
    parser.add_argument("--deploy-cmd", default=os.environ.get("DEPLOYER_DEPLOY_CMD", "./scripts/deploy.sh {env}"))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("DEPLOYER_TIMEOUT", "3600")))
    parser.add_argument("--public-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="one deployment cycle (default)")
    sub.add_parser("status", help="show the current deployment and the last events")
    manual = sub.add_parser("deploy", help="deploy a given ref")
    manual.add_argument("--ref", required=True)
    manual.add_argument("--skip-ci", action="store_true")
    rollback = sub.add_parser("rollback", help="roll back to a given ref (no CI gate)")
    rollback.add_argument("--to", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = parse_args(argv)
    try:
        deployer = Deployer(args)
    except DeployerError as exc:
        log.error("%s", exc)
        return 3
    if args.command == "status":
        return deployer.status()
    with single_instance(deployer.state_dir) as acquired:
        if not acquired:
            log.info("another deployer run is in progress, skipping")
            return 0
        try:
            if args.command == "deploy":
                return deployer.manual(args.ref, args.skip_ci, "manual")
            if args.command == "rollback":
                return deployer.manual(args.to, True, "manual rollback")
            return deployer.cycle()
        except DeployerError as exc:
            deployer.record_once("error", None, None, str(exc))  # e.g. GitHub unreachable: logged once, retried
            return 3


if __name__ == "__main__":
    sys.exit(main())
