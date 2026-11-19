"""Prometheus exporter for Dagster (`python -m datahub_ui.exporter`, port 9188).

Dagster OSS has no native Prometheus endpoint: this small service polls the GraphQL API
and exposes run, asset, check, freshness and daemon metrics for Prometheus / Grafana alerts.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter
from collections.abc import Iterable

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY, GaugeMetricFamily
from prometheus_client.registry import Collector

from datahub_ui.clients.dagster import DagsterClient

log = logging.getLogger("datahub.exporter")
FRESHNESS_CODES = {"HEALTHY": 0, "WARNING": 1, "DEGRADED": 2, "UNKNOWN": -1, "NOT_APPLICABLE": -1}
WINDOW_S = 24 * 3600


def snapshot(client: DagsterClient, now: float | None = None) -> dict:
    """Collect everything once (pure function of the API responses, unit-tested with fakes)."""
    now = now or time.time()
    runs = client.runs(limit=1000, created_after=now - WINDOW_S)
    by_job_status = Counter((r["jobName"], r["status"]) for r in runs)
    last_by_job: dict[str, dict] = {}
    for run in sorted(runs, key=lambda r: r["creationTime"]):
        if run["status"] in ("SUCCESS", "FAILURE", "CANCELED"):
            last_by_job[run["jobName"]] = run
    assets = client.assets()
    checks = []
    for asset in assets:
        if asset["has_checks"]:
            checks.extend(client.asset_checks(asset["key"]))
    return {
        "runs": by_job_status,
        "last": last_by_job,
        "in_progress": sum(1 for r in runs if r["status"] in ("STARTED", "STARTING")),
        "queued": sum(1 for r in runs if r["status"] == "QUEUED"),
        "assets": assets,
        "checks": checks,
        "daemons": client.daemon_health(),
    }


class DagsterCollector(Collector):
    def __init__(self, client: DagsterClient, interval: float = 30.0):
        self.client = client
        self.interval = interval
        self._snap: dict | None = None
        self._up = 0
        self._duration = 0.0
        self._lock = threading.Lock()

    def refresh(self) -> None:
        start = time.perf_counter()
        try:
            snap = snapshot(self.client)
            with self._lock:
                self._snap, self._up = snap, 1
        except Exception as exc:  # noqa: BLE001 - the exporter must keep running
            log.warning("Dagster scrape failed: %s", exc)
            with self._lock:
                self._up = 0
        self._duration = time.perf_counter() - start

    def loop(self) -> None:
        while True:
            self.refresh()
            time.sleep(self.interval)

    def collect(self) -> Iterable[GaugeMetricFamily]:
        with self._lock:
            snap, up = self._snap, self._up
        yield GaugeMetricFamily("datahub_dagster_up", "1 if the Dagster GraphQL API answered the last scrape", value=up)
        yield GaugeMetricFamily(
            "datahub_exporter_scrape_duration_seconds", "Duration of the last scrape", value=self._duration
        )
        if not snap:
            return
        runs = GaugeMetricFamily(
            "datahub_dagster_runs_24h", "Runs created during the last 24 h", labels=["job", "status"]
        )
        for (job, status), count in snap["runs"].items():
            runs.add_metric([job, status], count)
        yield runs
        duration = GaugeMetricFamily(
            "datahub_dagster_last_run_duration_seconds", "Duration of the last finished run", labels=["job"]
        )
        success = GaugeMetricFamily(
            "datahub_dagster_last_run_success", "1 if the last finished run succeeded", labels=["job"]
        )
        end = GaugeMetricFamily(
            "datahub_dagster_last_run_end_timestamp_seconds", "End time of the last finished run", labels=["job"]
        )
        for job, run in snap["last"].items():
            if run["duration"] is not None:
                duration.add_metric([job], run["duration"])
            success.add_metric([job], 1 if run["status"] == "SUCCESS" else 0)
            end.add_metric([job], run["endTime"] or 0)
        yield duration
        yield success
        yield end
        yield GaugeMetricFamily(
            "datahub_dagster_runs_in_progress", "Runs currently executing", value=snap["in_progress"]
        )
        yield GaugeMetricFamily("datahub_dagster_runs_queued", "Runs waiting in the queue", value=snap["queued"])
        fresh = GaugeMetricFamily(
            "datahub_dagster_asset_freshness", "0 healthy, 1 warning, 2 degraded, -1 unknown", labels=["asset"]
        )
        last_mat = GaugeMetricFamily(
            "datahub_dagster_asset_last_materialization_timestamp_seconds",
            "Last materialization time",
            labels=["asset", "group"],
        )
        for asset in snap["assets"]:
            if asset.get("freshness"):
                fresh.add_metric([asset["key"]], FRESHNESS_CODES.get(asset["freshness"], -1))
            if asset.get("last_materialization"):
                last_mat.add_metric([asset["key"], asset["group"]], asset["last_materialization"])
        yield fresh
        yield last_mat
        check = GaugeMetricFamily(
            "datahub_dagster_asset_check_passed",
            "1 if the latest execution of the check passed, 0 if it failed",
            labels=["asset", "check", "severity"],
        )
        for c in snap["checks"]:
            if c["status"] in ("SUCCEEDED", "FAILED", "EXECUTION_FAILED"):
                check.add_metric([c["asset"], c["name"], c["severity"] or ""], 1 if c["status"] == "SUCCEEDED" else 0)
        yield check
        daemon = GaugeMetricFamily(
            "datahub_dagster_daemon_healthy", "1 if the Dagster daemon is healthy", labels=["daemon"]
        )
        for d in snap["daemons"]:
            daemon.add_metric([d["daemonType"]], 1 if d["healthy"] else 0)
        yield daemon


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    url = os.getenv("DAGSTER_GRAPHQL_URL", "http://dagster-webserver:3000/dagster/graphql")
    port = int(os.getenv("EXPORTER_PORT", "9188"))
    collector = DagsterCollector(
        DagsterClient(url, location=os.getenv("DAGSTER_LOCATION", "datahub")),
        interval=float(os.getenv("EXPORTER_INTERVAL", "30")),
    )
    REGISTRY.register(collector)
    start_http_server(port)
    log.info("Dagster exporter listening on :%s (source %s)", port, url)
    collector.loop()


if __name__ == "__main__":
    main()
