import dagster as dg

EXPECTED_JOBS = {"daily_partition_job", "dbt_marts_job", "landing_files_job", "dbt_docs_job"}
EXPECTED_SCHEDULES = {"daily_partition_schedule", "weekly_marts_rebuild_schedule"}
EXPECTED_SENSORS = {"landing_file_sensor", "refresh_docs_after_daily_success", "failure_notification_sensor"}
EXPECTED_ASSETS = {
    ("raw", "customers"),
    ("raw", "orders"),
    ("raw", "payments"),
    ("raw", "landing_files"),
    ("reference", "country_codes"),
    ("staging", "stg_customers"),
    ("staging", "stg_orders"),
    ("staging", "stg_payments"),
    ("intermediate", "int_order_payments"),
    ("marts", "fct_orders"),
    ("marts", "dim_customers"),
    ("marts", "agg_daily_revenue"),
}


def test_definitions_are_loadable(defs):
    dg.Definitions.validate_loadable(defs)


def test_jobs_schedules_sensors(defs):
    repo = defs.get_repository_def()
    assert EXPECTED_JOBS <= {job.name for job in repo.get_all_jobs()}
    assert EXPECTED_SCHEDULES <= {s.name for s in repo.schedule_defs}
    assert EXPECTED_SENSORS <= {s.name for s in repo.sensor_defs}


def test_asset_keys_follow_duckdb_layout(defs):
    keys = {tuple(k.path) for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert EXPECTED_ASSETS <= keys


def test_groups(defs):
    graph = defs.resolve_asset_graph()
    groups = {tuple(k.path): graph.get(k).group_name for k in graph.get_all_asset_keys()}
    assert groups[("raw", "orders")] == "ingestion"
    assert groups[("staging", "stg_orders")] == "staging"
    assert groups[("marts", "fct_orders")] == "marts_core"
    assert groups[("marts", "agg_daily_revenue")] == "marts_finance"
    assert groups[("reference", "country_codes")] == "reference"


def test_partitioning(defs):
    graph = defs.resolve_asset_graph()
    partitioned = {tuple(k.path) for k in graph.get_all_asset_keys() if graph.get(k).partitions_def}
    assert partitioned == {("raw", "orders"), ("raw", "payments"), ("marts", "fct_orders")}


def test_dbt_tests_are_asset_checks(defs):
    graph = defs.resolve_asset_graph()
    check_names = {c.name for c in graph.asset_check_keys}
    assert "unique_fct_orders_order_id" in check_names
    assert "customers_email_completeness" in check_names
    assert "orders_volume_anomaly" in check_names
    assert any(name.startswith("relationships_stg_orders_customer_id") for name in check_names)


def test_freshness_policies(defs):
    graph = defs.resolve_asset_graph()
    for key in (["raw", "orders"], ["raw", "customers"], ["marts", "fct_orders"], ["marts", "agg_daily_revenue"]):
        assert graph.get(dg.AssetKey(key)).freshness_policy is not None, key
    assert graph.get(dg.AssetKey(["staging", "stg_orders"])).freshness_policy is None


def test_duckdb_writers_share_a_single_pool(defs):
    """Every op writing to DuckDB must be in the `duckdb` pool (limit 1 in dagster.yaml)."""
    for assets_def in defs.assets:
        if isinstance(assets_def, dg.AssetsDefinition):
            assert assets_def.op.pool == "duckdb", assets_def.node_def.name
