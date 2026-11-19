import pandas as pd
import pytest

from datahub_ui.clients.dbt_artifacts import DbtArtifacts
from datahub_ui.incidents import classify
from datahub_ui.lineage import (
    build_graph,
    catalog_schema,
    column_lineage,
    downstream,
    impact_analysis,
    reverse,
    to_dot,
    upstream,
)
from datahub_ui.pages.m07_backfill import missing_partitions
from datahub_ui.perf import compare_steps, detect_slowdowns, duration_stats, runs_frame

ASSETS = [
    {"key": "raw/orders", "deps": []},
    {"key": "staging/stg_orders", "deps": ["raw/orders"]},
    {"key": "marts/fct_orders", "deps": ["staging/stg_orders"]},
    {"key": "marts/agg_daily_revenue", "deps": ["marts/fct_orders"]},
    {"key": "raw/customers", "deps": []},
]


def test_graph_traversal():
    g = build_graph(ASSETS)
    assert downstream(g, "raw/orders") == {"staging/stg_orders": 1, "marts/fct_orders": 2, "marts/agg_daily_revenue": 3}
    assert downstream(g, "raw/orders", max_depth=1) == {"staging/stg_orders": 1}
    assert upstream(g, "marts/agg_daily_revenue") == {"marts/fct_orders": 1, "staging/stg_orders": 2, "raw/orders": 3}
    assert reverse(g)["staging/stg_orders"] == {"raw/orders"}


def test_impact_analysis_with_exposures():
    g = build_graph(ASSETS)
    exposures = [{"label": "Dashboard finance", "depends_on": {"nodes": ["model.datahub_dbt.agg_daily_revenue"]}}]
    impact = impact_analysis(g, "staging/stg_orders", exposures)
    assert impact["count"] == 2
    assert impact["by_layer"] == {"marts": ["marts/fct_orders", "marts/agg_daily_revenue"]}
    assert impact["exposures"] == ["Dashboard finance"]
    assert impact_analysis(g, "raw/customers")["count"] == 0


def test_dot_rendering_contains_nodes_and_edges():
    dot = to_dot(build_graph(ASSETS), focus="marts/fct_orders")
    assert dot.startswith("digraph")
    assert '"raw/orders" -> "staging/stg_orders";' in dot
    assert "penwidth=2.5" in dot


def test_column_lineage_simple_sql():
    sql = "select o.amount * 2 as doubled, c.name from staging.orders o join staging.customers c on o.cid = c.id"
    schema = {"staging": {"orders": {"amount": "int", "cid": "int"}, "customers": {"id": "int", "name": "text"}}}
    assert column_lineage(sql, "doubled", schema) == [{"table": "staging.orders", "column": "amount"}]
    assert column_lineage(sql, "name", schema) == [{"table": "staging.customers", "column": "name"}]
    assert column_lineage("", "x") == []


def test_column_lineage_on_real_compiled_dbt_model(artifacts_dir):
    art = DbtArtifacts.load(artifacts_dir)
    _, compiled = art.sql("model.datahub_dbt.agg_daily_revenue")
    sources = column_lineage(compiled, "revenue", catalog_schema(art.catalog))
    assert {"table": "marts.fct_orders", "column": "order_amount"} in sources


def test_dbt_artifacts_catalog(artifacts_dir):
    art = DbtArtifacts.load(artifacts_dir)
    assert art.available
    nodes = {n["asset_key"]: n for n in art.documented_nodes()}
    assert {"marts/fct_orders", "staging/stg_orders", "reference/country_codes", "raw/orders"} <= set(nodes)
    fct = nodes["marts/fct_orders"]
    assert fct["materialized"] == "incremental"
    cols = {c["name"]: c for c in fct["columns"]}
    assert "unique" in cols["order_id"]["tests"] and "not_null" in cols["order_id"]["tests"]
    assert cols["order_amount"]["type"]
    assert {c["name"]: c for c in nodes["staging/stg_customers"]["columns"]}["email"]["pii"] is True
    raw, compiled = art.sql(fct["unique_id"])
    assert "incremental_window" in raw and "select" in compiled.lower()
    assert art.exposures()[0]["name"] == "datahub_finance_dashboard"


def test_dbt_artifacts_missing_dir(tmp_path):
    assert not DbtArtifacts.load(tmp_path).available


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ('Catalog Error: Table with name "x" does not exist', "Erreur SQL / dbt"),
        ("IO Error: Could not set lock on file", "Verrou DuckDB / concurrence"),
        ("psycopg.OperationalError: connection refused", "Connexion / réseau"),
        (
            "datahub_pipelines.generators.SimulatedSourceError: Simulated source unavailable: HTTP 503 (attempt 4)",
            "Connexion / réseau",
        ),
        ("HTTPError: 502 Bad Gateway", "Connexion / réseau"),
        ("Blocking check 'customers_email_completeness' failed", "Qualité des données (test dbt / check)"),
        ("Killed: out of memory", "Ressources (mémoire / disque)"),
        ("KeyError: 'amount'", "Erreur Python"),
        ("something odd", "Inconnu"),
    ],
)
def test_error_classification(message, category):
    assert classify(message).category == category


def _runs():
    base = [
        {
            "runId": f"r{i}",
            "jobName": "daily",
            "status": "SUCCESS",
            "startTime": 1000.0 * i,
            "endTime": 1000.0 * i + 60,
            "duration": 60.0,
            "tags": {},
        }
        for i in range(8)
    ]
    base.append(
        {
            "runId": "slow",
            "jobName": "daily",
            "status": "SUCCESS",
            "startTime": 9000.0,
            "endTime": 9200.0,
            "duration": 200.0,
            "tags": {"dagster/partition": "2026-01-09"},
        }
    )
    base.append(
        {
            "runId": "ko",
            "jobName": "daily",
            "status": "FAILURE",
            "startTime": 9500.0,
            "endTime": 9510.0,
            "duration": 10.0,
            "tags": {},
        }
    )
    return base


def test_duration_stats_and_slowdown_detection():
    df = runs_frame(_runs())
    stats = duration_stats(df).set_index("jobName")
    assert stats.loc["daily", "runs"] == 10
    assert stats.loc["daily", "success_rate"] == 90.0
    slow = detect_slowdowns(df, factor=1.5)
    assert list(slow["runId"]) == ["slow"]
    assert slow.iloc[0]["ratio"] == pytest.approx(3.33, 0.01)


def test_runs_frame_empty():
    assert runs_frame([]).empty and duration_stats(runs_frame([])).empty


def test_compare_steps():
    a = [{"stepKey": "s1", "startTime": 0, "endTime": 10}, {"stepKey": "s2", "startTime": 0, "endTime": 5}]
    b = [{"stepKey": "s1", "startTime": 0, "endTime": 15}]
    df = compare_steps(a, b).set_index("step")
    assert df.loc["s1", "delta_s"] == 5
    assert pd.isna(df.loc["s2", "run_b_s"])


def test_missing_partitions():
    keys = [f"2026-01-0{i}" for i in range(1, 8)]
    ranges = [
        {"startKey": "2026-01-01", "endKey": "2026-01-03", "status": "MATERIALIZED"},
        {"startKey": "2026-01-05", "endKey": "2026-01-05", "status": "FAILED"},
    ]
    assert missing_partitions(keys, ranges) == ["2026-01-04", "2026-01-05", "2026-01-06", "2026-01-07"]
    assert missing_partitions(keys, ranges, include_failed=False) == ["2026-01-04", "2026-01-06", "2026-01-07"]
