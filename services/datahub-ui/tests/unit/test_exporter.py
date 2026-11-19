from prometheus_client import CollectorRegistry, generate_latest

from datahub_ui.exporter import DagsterCollector, snapshot
from datahub_ui.testing.fakes import FakeDagster


def test_snapshot_from_fake_dagster():
    snap = snapshot(FakeDagster())
    assert snap["runs"][("daily_partition_job", "SUCCESS")] >= 1
    assert "daily_partition_job" in snap["last"]
    assert snap["checks"]


def test_collector_exposes_metrics():
    collector = DagsterCollector(FakeDagster())
    registry = CollectorRegistry()
    registry.register(collector)
    before = generate_latest(registry).decode()
    assert "datahub_dagster_up 0.0" in before
    collector.refresh()
    text = generate_latest(registry).decode()
    assert "datahub_dagster_up 1.0" in text
    for metric in (
        "datahub_dagster_runs_24h",
        "datahub_dagster_last_run_success",
        "datahub_dagster_asset_freshness",
        "datahub_dagster_asset_check_passed",
        "datahub_dagster_daemon_healthy",
    ):
        assert metric in text
    assert (
        'datahub_dagster_asset_check_passed{asset="raw/orders",check="orders_volume_anomaly",severity="WARN"} 0.0'
        in text
    )


def test_collector_survives_backend_failure():
    class Broken(FakeDagster):
        def runs(self, *a, **k):
            raise RuntimeError("down")

    collector = DagsterCollector(Broken())
    collector.refresh()
    registry = CollectorRegistry()
    registry.register(collector)
    assert "datahub_dagster_up 0.0" in generate_latest(registry).decode()
