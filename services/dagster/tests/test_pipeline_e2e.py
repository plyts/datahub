"""End-to-end execution of the real pipelines (Python ingestion + dbt Core on DuckDB)."""

import json
import os
from datetime import date
from pathlib import Path

import duckdb
import pytest

from datahub_pipelines import generators

pytestmark = pytest.mark.slow

DAYS = ["2026-03-01", "2026-03-02", "2026-03-03"]


def _query(sql: str):
    with duckdb.connect(os.environ["DUCKDB_PATH"], read_only=True) as conn:
        return conn.execute(sql).fetchall()


@pytest.fixture(scope="module")
def daily_runs(defs, instance):
    job = defs.resolve_job_def("daily_partition_job")
    return [job.execute_in_process(partition_key=day, instance=instance, raise_on_error=False) for day in DAYS]


def test_daily_runs_succeed(daily_runs):
    for result in daily_runs:
        assert result.success, [e.message for e in result.all_events if e.is_failure]


def test_all_quality_checks_pass(daily_runs):
    for result in daily_runs:
        evaluations = [
            e.event_specific_data for e in result.all_events if e.event_type_value == "ASSET_CHECK_EVALUATION"
        ]
        assert len(evaluations) >= 35
        failed = [ev.check_name for ev in evaluations if not ev.passed]
        assert failed == []


def test_raw_volumes_match_generators(daily_runs):
    expected = sum(generators.expected_order_count(date.fromisoformat(d)) for d in DAYS)
    assert (
        _query("select count(*) from raw.orders where _partition_date between '2026-03-01' and '2026-03-03'")[0][0]
        == expected
    )


def test_marts_are_built(daily_runs):
    expected = sum(generators.expected_order_count(date.fromisoformat(d)) for d in DAYS)
    assert (
        _query("select count(*) from marts.fct_orders where order_date between '2026-03-01' and '2026-03-03'")[0][0]
        == expected
    )
    assert (
        _query("select count(*) from marts.agg_daily_revenue where order_date between '2026-03-01' and '2026-03-03'")[
            0
        ][0]
        == 3
    )
    assert _query("select count(*) from marts.dim_customers")[0][0] == generators.NB_CUSTOMERS
    assert _query("select count(*) from reference.country_codes")[0][0] == 10


def test_row_count_metadata_on_dbt_materializations(daily_runs):
    mats = {
        tuple(e.asset_key.path): e.event_specific_data.materialization.metadata
        for e in daily_runs[-1].all_events
        if e.event_type_value == "ASSET_MATERIALIZATION"
    }
    assert mats[("marts", "fct_orders")]["dagster/row_count"].value > 0
    assert "dagster/column_schema" in mats[("marts", "dim_customers")]


def test_rerun_is_idempotent(defs, instance, daily_runs):
    before = _query("select count(*), sum(order_amount) from marts.fct_orders")[0]
    result = defs.resolve_job_def("daily_partition_job").execute_in_process(partition_key=DAYS[1], instance=instance)
    assert result.success
    assert _query("select count(*), sum(order_amount) from marts.fct_orders")[0] == before
    assert _query("select count(*) - count(distinct order_id) from raw.orders")[0][0] == 0


def test_backfill_range_in_a_single_run(defs, instance, daily_runs):
    """BackfillPolicy.single_run(): a range of partitions is processed by one run."""
    job = defs.resolve_job_def("daily_partition_job")
    result = job.execute_in_process(
        instance=instance,
        tags={"dagster/asset_partition_range_start": "2026-03-04", "dagster/asset_partition_range_end": "2026-03-06"},
    )
    assert result.success
    days = _query(
        "select count(distinct order_date) from marts.fct_orders where order_date between '2026-03-04' and '2026-03-06'"
    )
    assert days[0][0] == 3


def test_docs_job_publishes_artifacts(defs, instance, daily_runs):
    result = defs.resolve_job_def("dbt_docs_job").execute_in_process(instance=instance)
    assert result.success
    artifacts = Path(os.environ["DBT_ARTIFACTS_DIR"])
    for name in ("manifest.json", "catalog.json", "index.html"):
        assert (artifacts / name).exists(), name
    catalog = json.loads((artifacts / "catalog.json").read_text())
    assert "model.datahub_dbt.fct_orders" in catalog["nodes"]
