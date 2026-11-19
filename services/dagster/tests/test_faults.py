"""Fault injection in the simulated source: every scenario of the CI/CD guide, executed for real.

Each failing scenario ends with its recovery (same partition re-run without fault), which also
leaves the shared test database clean for the other test modules.
"""

import os
from datetime import date

import duckdb
import pytest

from datahub_pipelines import generators

pytestmark = pytest.mark.slow

DAY = "2026-04-15"
FAULT_TAGS = {"datahub/scenario": "test"}


def run_config(fault: str) -> dict:
    op = "raw__customers" if fault in generators.CUSTOMER_FAULTS else "raw__orders"
    return {"ops": {op: {"config": {"fault": fault}}}}


def run_daily(defs, instance, partition: str, fault: str = "none"):
    job = defs.resolve_job_def("daily_partition_job")
    return job.execute_in_process(
        partition_key=partition,
        instance=instance,
        raise_on_error=False,
        run_config=run_config(fault) if fault != "none" else None,
        tags=FAULT_TAGS,
    )


def failed_checks(result) -> set[str]:
    return {
        e.event_specific_data.check_name
        for e in result.all_events
        if e.event_type_value == "ASSET_CHECK_EVALUATION" and not e.event_specific_data.passed
    }


def step_events(result, event_type: str) -> list[str]:
    return [e.step_key for e in result.all_events if e.event_type_value == event_type]


def failure_text(result) -> str:
    texts = []
    for e in result.all_events:
        if e.event_type_value == "STEP_FAILURE":
            data = e.event_specific_data
            texts.append(data.error.to_string() if data.error else "")
            if data.user_failure_data:
                texts.append(data.user_failure_data.description or "")
    return "\n".join(texts)


def query(sql: str):
    with duckdb.connect(os.environ["DUCKDB_PATH"], read_only=True) as conn:
        return conn.execute(sql).fetchall()


# ------------------------------------------------------------------ generators (unit)
def test_fault_catalog_is_consistent():
    assert set(generators.FAULTS) == {"none"} | generators.CUSTOMER_FAULTS | generators.ORDER_FAULTS


def test_volume_spike_keeps_the_normal_orders_and_adds_four_times_more():
    day = date(2026, 4, 15)
    normal, spike = generators.generate_orders(day), generators.orders_with_fault(day, "volume_spike")
    assert len(spike) == 5 * len(normal)
    assert spike.head(len(normal)).equals(normal)
    assert spike["order_id"].is_unique


def test_negative_amounts_and_null_emails():
    orders = generators.orders_with_fault(date(2026, 4, 15), "negative_amounts")
    assert (orders["amount"] < 0).sum() == len(orders[orders.index % 20 == 0])
    customers = generators.customers_with_fault("null_emails")
    assert customers["email"].isna().sum() == generators.NB_CUSTOMERS // 10


@pytest.mark.parametrize(
    ("fault", "attempt", "fails"),
    [("source_flaky", 0, True), ("source_flaky", 1, False), ("source_unavailable", 3, True), ("none", 0, False)],
)
def test_simulated_source_availability(fault, attempt, fails):
    if fails:
        with pytest.raises(generators.SimulatedSourceError, match="503"):
            generators.check_source(fault, attempt)
    else:
        generators.check_source(fault, attempt)


# ------------------------------------------------------------------ scenarios (real runs)
def test_nominal_run(defs, instance):
    result = run_daily(defs, instance, DAY)
    assert result.success
    assert failed_checks(result) == set()


def test_exception_flaky_source_is_retried_and_succeeds(defs, instance):
    result = run_daily(defs, instance, DAY, "source_flaky")
    assert result.success
    assert step_events(result, "STEP_UP_FOR_RETRY") == ["raw__orders"]


def test_error_negative_amounts_fails_the_run_then_recovers(defs, instance):
    result = run_daily(defs, instance, DAY, "negative_amounts")
    assert not result.success
    failed = failed_checks(result)
    assert any(name.startswith("not_negative_stg_orders_amount") for name in failed), failed
    assert "dbt_upstream" in step_events(result, "STEP_FAILURE")
    assert step_events(result, "STEP_UP_FOR_RETRY") == [], "a data error must not be retried"
    assert query(f"select count(*) from raw.orders where amount < 0 and _partition_date = '{DAY}'")[0][0] > 0
    # Recovery: the same partition re-run without the fault replaces the bad rows (idempotent ingestion).
    recovery = run_daily(defs, instance, DAY)
    assert recovery.success
    assert query("select count(*) from raw.orders where amount < 0")[0][0] == 0


def test_error_null_emails_blocks_downstream_then_recovers(defs, instance):
    result = run_daily(defs, instance, DAY, "null_emails")
    assert not result.success
    assert "customers_email_completeness" in failed_checks(result)
    assert "raw__customers" in step_events(result, "STEP_SUCCESS")  # loaded, but blocked by its check
    assert "dbt_downstream" not in step_events(result, "STEP_SUCCESS")
    assert run_daily(defs, instance, DAY).success
    assert query("select count(*) from raw.customers where email is null")[0][0] == 0


def test_exception_volume_spike_is_flagged_without_failing(defs, instance):
    job = defs.resolve_job_def("daily_partition_job")
    history = job.execute_in_process(
        instance=instance,
        tags={"dagster/asset_partition_range_start": "2026-04-01", "dagster/asset_partition_range_end": "2026-04-14"},
    )
    assert history.success
    result = run_daily(defs, instance, DAY, "volume_spike")
    assert result.success, "a volume anomaly is a warning, not a failure"
    assert "orders_volume_anomaly" in failed_checks(result)
    expected = 5 * generators.expected_order_count(date.fromisoformat(DAY))
    assert query(f"select count(*) from raw.orders where _partition_date = '{DAY}'")[0][0] == expected
    recovery = run_daily(defs, instance, DAY)
    assert recovery.success
    assert failed_checks(recovery) == set()
    normal = generators.expected_order_count(date.fromisoformat(DAY))
    # the 4x extra orders must disappear from the incremental mart too, not only from raw
    assert query(f"select count(*) from marts.fct_orders where order_date = '{DAY}'")[0][0] == normal


def test_unknown_fault_is_rejected(defs, instance):
    result = run_daily(defs, instance, DAY, "meteor_strike")
    assert not result.success
    assert "Panne simulée inconnue" in failure_text(result)


def test_faults_are_refused_in_production(defs, instance, monkeypatch):
    monkeypatch.setenv("DATAHUB_ENV", "prod")
    result = run_daily(defs, instance, DAY, "negative_amounts")
    assert not result.success
    assert "interdite en production" in failure_text(result)
