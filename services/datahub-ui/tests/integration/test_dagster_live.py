"""Integration tests of the Data Hub Dagster client against a live Dagster deployment.

    DAGSTER_GRAPHQL_URL=http://localhost:3000/dagster/graphql
    DATAHUB_TEST_PG_URL=postgresql://datahub_app:...@localhost:5432/datahub  (custom pipeline scenario)

Validates every GraphQL operation used by the UI against the real schema, and runs real pipelines.
"""

import os
import time
import uuid
from datetime import date, timedelta

import pytest

from datahub_ui.clients.dagster import DagsterClient
from datahub_ui.exporter import snapshot

pytestmark = pytest.mark.integration
URL = os.getenv("DAGSTER_GRAPHQL_URL")
if not URL:
    pytest.skip("DAGSTER_GRAPHQL_URL not set", allow_module_level=True)

TERMINAL = {"SUCCESS", "FAILURE", "CANCELED"}
PARTITION = (date.today() - timedelta(days=2)).isoformat()


@pytest.fixture(scope="module")
def client() -> DagsterClient:
    c = DagsterClient(URL, timeout=60)
    deadline = time.time() + 120
    while time.time() < deadline:
        if c.ping() and c.locations() and c.locations()[0]["status"] == "LOADED":
            return c
        time.sleep(3)
    pytest.fail("Dagster code location not loaded")


def wait_run(client, run_id, timeout=420) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.run(run_id)
        if run["status"] in TERMINAL:
            return run
        time.sleep(3)
    pytest.fail(f"run {run_id} not finished after {timeout}s")


def test_workspace_and_daemons(client):
    assert client.locations()[0]["error"] is None
    daemons = {d["daemonType"]: d["healthy"] for d in client.daemon_health()}
    assert daemons.get("SCHEDULER") and daemons.get("SENSOR") and daemons.get("QUEUED_RUN_COORDINATOR")


def test_definitions_visible(client):
    assert {"daily_partition_job", "dbt_marts_job", "landing_files_job", "dbt_docs_job"} <= {j["name"] for j in client.jobs()}
    schedules = {s["name"]: s for s in client.schedules()}
    assert schedules["daily_partition_schedule"]["status"] == "RUNNING"
    assert len(schedules["daily_partition_schedule"]["next_ticks"]) == 5
    sensors = {s["name"] for s in client.sensors()}
    assert {"landing_file_sensor", "failure_notification_sensor", "refresh_docs_after_daily_success"} <= sensors
    keys = {a["key"] for a in client.assets()}
    assert {"raw/orders", "marts/fct_orders", "marts/dim_customers", "reference/country_codes"} <= keys
    assert len(client.partition_keys("raw/orders")) > 100


def test_daily_pipeline_end_to_end(client):
    run_id = client.launch_job("daily_partition_job", partition_key=PARTITION, tags={"datahub/user": "it"})
    run = wait_run(client, run_id)
    assert run["status"] == "SUCCESS", run
    assert run["tags"]["datahub/user"] == "it" and run["tags"]["dagster/partition"] == PARTITION
    assert all(s["status"] == "SUCCESS" for s in run["stepStats"])
    events, _, _ = client.run_events(run_id)
    assert any(e.get("eventType") == "RUN_SUCCESS" for e in events)
    mats = client.materializations("marts/fct_orders", limit=5)
    assert mats[0]["metadata"]["dagster/row_count"] > 0
    checks = client.asset_checks("marts/fct_orders")
    assert checks and all(c["status"] == "SUCCEEDED" for c in checks if c["run_id"] == run_id)
    history = client.check_history("raw/orders", "orders_volume_anomaly")
    assert history[0]["run_id"] == run_id
    assert any(r["status"] == "MATERIALIZED" for r in client.partition_ranges("raw/orders"))


def test_reexecution(client):
    parent = client.runs(limit=1, job_name="daily_partition_job", statuses=["SUCCESS"])[0]
    child = wait_run(client, client.reexecute(parent["runId"], strategy="ALL_STEPS", tags={"datahub/user": "it"}))
    assert child["status"] == "SUCCESS" and child["parentRunId"] == parent["runId"]


def test_backfill_of_two_partitions(client):
    days = [(date.today() - timedelta(days=d)).isoformat() for d in (5, 4)]
    backfill_id = client.launch_backfill(["raw/orders", "raw/payments", "marts/fct_orders"], partition_names=days,
                                         title="it backfill", tags={"datahub/user": "it"})
    deadline = time.time() + 420
    while time.time() < deadline:
        backfill = next(b for b in client.backfills() if b["id"] == backfill_id)
        if backfill["status"] not in ("REQUESTED", "CANCELING"):
            break
        time.sleep(5)
    assert backfill["status"] in ("COMPLETED", "COMPLETED_SUCCESS"), backfill
    assert backfill["numPartitions"] == 2


def test_schedule_and_sensor_toggle(client):
    assert client.set_schedule("weekly_marts_rebuild_schedule", True) == "RUNNING"
    assert client.set_schedule("weekly_marts_rebuild_schedule", False) == "STOPPED"
    assert client.set_sensor("landing_file_sensor", False) == "STOPPED"
    assert client.set_sensor("landing_file_sensor", True) == "RUNNING"


@pytest.mark.skipif(not os.getenv("DATAHUB_TEST_PG_URL"), reason="DATAHUB_TEST_PG_URL not set")
def test_custom_pipeline_created_from_ui_runs_in_dagster(client):
    from datahub_ui.db.repository import AppDB

    db = AppDB(os.environ["DATAHUB_TEST_PG_URL"])
    name = f"it_finance_{uuid.uuid4().hex[:6]}"
    db.save_pipeline({"name": name, "selection_type": "dagster", "selection": "group:marts_finance",
                      "cron": "0 7 * * 1-5"}, "it")
    try:
        assert client.reload_location() == "LOADED"
        assert f"custom_{name}" in {j["name"] for j in client.jobs()}
        assert f"custom_{name}_schedule" in {s["name"] for s in client.schedules()}
        run = wait_run(client, client.launch_job(f"custom_{name}", tags={"datahub/user": "it"}))
        assert run["status"] == "SUCCESS"
        assert {s["stepKey"] for s in run["stepStats"]} == {"dbt_downstream"}
    finally:
        db.delete_pipeline(name)
        client.reload_location()
    assert f"custom_{name}" not in {j["name"] for j in client.jobs()}


def test_exporter_snapshot_on_live_instance(client):
    snap = snapshot(client)
    assert snap["runs"] and snap["assets"] and snap["daemons"]
