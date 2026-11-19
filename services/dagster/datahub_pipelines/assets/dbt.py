"""dbt Core models exposed as Dagster assets (via dagster-dbt).

The dbt project is split into three subsettable multi-assets so that the
incremental (partitioned) model can sit between unpartitioned layers:

    sources (raw.*) -> [dbt_upstream]  staging + intermediate + seeds   (unpartitioned)
                    -> [dbt_partitioned] fct_orders                     (daily partitions)
                    -> [dbt_downstream] marts                           (unpartitioned)

dbt tests are exposed as Dagster asset checks (Data Quality Center).
"""

import json
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

import dagster as dg
import duckdb as duckdb_lib
from dagster_dbt import DagsterDbtTranslator, DagsterDbtTranslatorSettings, DbtCliResource, dbt_assets

from datahub_pipelines.freshness import policy_for
from datahub_pipelines.partitions import DUCKDB_POOL, daily_partitions
from datahub_pipelines.resources import dbt_project
from datahub_pipelines.settings import get_settings

UPSTREAM_SELECT = "tag:staging tag:intermediate resource_type:seed"
PARTITIONED_SELECT = "tag:partitioned"
DOWNSTREAM_SELECT = "tag:marts"

# A failing dbt test is a data problem, never retried. Only a transient DuckDB lock is (see _stream).
LOCK_MARKERS = ("could not set lock", "conflicting lock")


class DataHubDbtTranslator(DagsterDbtTranslator):
    """Asset keys follow the DuckDB layout: [schema, table] (e.g. ["marts", "fct_orders"])."""

    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> dg.AssetKey:
        if dbt_resource_props["resource_type"] == "source":
            return super().get_asset_key(dbt_resource_props)
        return dg.AssetKey([dbt_resource_props["schema"], dbt_resource_props["name"]])

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        if dbt_resource_props["resource_type"] == "seed":
            return "reference"
        fqn = dbt_resource_props.get("fqn", [])
        layers = list(fqn[1:-1])  # drop project name and model name
        return "_".join(layers) if layers else "dbt"

    def get_asset_spec(self, manifest: Mapping[str, Any], unique_id: str, project: Any) -> dg.AssetSpec:
        spec = super().get_asset_spec(manifest, unique_id, project)
        policy = policy_for(spec.key)
        return spec.replace_attributes(freshness_policy=policy) if policy else spec


translator = DataHubDbtTranslator(
    settings=DagsterDbtTranslatorSettings(enable_asset_checks=True, enable_code_references=False)
)


def table_stats(conn, asset_key: dg.AssetKey) -> dict[str, Any]:
    """Row count and column schema of the DuckDB table/view behind a dbt asset ([schema, name])."""
    if len(asset_key.path) != 2:
        return {}
    schema, name = asset_key.path
    columns = conn.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_schema = ? and table_name = ? order by ordinal_position",
        [schema, name],
    ).fetchall()
    if not columns:
        return {}
    row_count = conn.execute(f'select count(*) from "{schema}"."{name}"').fetchone()[0]
    return {
        "dagster/row_count": int(row_count),
        "dagster/column_schema": dg.TableSchema(
            columns=[dg.TableColumn(name=col, type=str(dtype)) for col, dtype in columns]
        ),
        "dagster/relation_identifier": f"{schema}.{name}",
    }


def enrich_with_table_stats(events: Iterable[Any], database: str) -> Iterator[Any]:
    """Add row counts and column schemas to dbt materializations.

    DuckDB accepts a single writer process: the stats are read *after* the dbt
    sub-process has exited (the events are buffered), with a read-only connection
    that is closed immediately. dagster-dbt's `fetch_row_counts()` cannot be used
    because it queries the database while dbt still holds the write lock.
    """
    buffered: list[Any] = []
    failure: Exception | None = None
    try:
        for event in events:
            buffered.append(event)
    except Exception as exc:  # noqa: BLE001 - dbt failed (e.g. a test of severity error): report what ran first
        failure = exc
    yield from _with_stats(buffered, database)
    if failure is not None:
        if any(marker in str(failure).lower() for marker in LOCK_MARKERS):
            raise dg.RetryRequested(max_retries=2, seconds_to_wait=15) from failure
        raise failure


def _with_stats(events: list[Any], database: str) -> Iterator[Any]:
    try:
        conn = duckdb_lib.connect(database, read_only=True)
    except duckdb_lib.Error:
        yield from events
        return
    try:
        for event in events:
            key = getattr(event, "asset_key", None) if isinstance(event, dg.AssetMaterialization) else None
            if isinstance(event, dg.Output):
                key = dg.AssetKey(event.output_name.split("__")) if "__" in event.output_name else None
            if key is not None:
                try:
                    stats = table_stats(conn, key)
                except duckdb_lib.Error:
                    stats = {}
                if stats:
                    event = event.with_metadata({**event.metadata, **stats})
            yield event
    finally:
        conn.close()


def _stream(invocation):
    return enrich_with_table_stats(invocation.stream(), get_settings().duckdb_path)


@dbt_assets(
    manifest=dbt_project.manifest_path,
    project=dbt_project,
    select=UPSTREAM_SELECT,
    name="dbt_upstream",
    dagster_dbt_translator=translator,
    pool=DUCKDB_POOL,
)
def dbt_upstream_assets(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from _stream(dbt.cli(["build"], context=context))


def partition_vars(context: dg.AssetExecutionContext) -> dict[str, str]:
    """dbt vars for the partition window [min_date, max_date) of the current run (supports ranges)."""
    window = context.partition_time_window
    return {"min_date": window.start.strftime("%Y-%m-%d"), "max_date": window.end.strftime("%Y-%m-%d")}


@dbt_assets(
    manifest=dbt_project.manifest_path,
    project=dbt_project,
    select=PARTITIONED_SELECT,
    name="dbt_partitioned",
    partitions_def=daily_partitions,
    backfill_policy=dg.BackfillPolicy.single_run(),
    dagster_dbt_translator=translator,
    pool=DUCKDB_POOL,
)
def dbt_partitioned_assets(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    dbt_vars = partition_vars(context)
    context.log.info("dbt partition window: %s", dbt_vars)
    yield from _stream(dbt.cli(["build", "--vars", json.dumps(dbt_vars)], context=context))


@dbt_assets(
    manifest=dbt_project.manifest_path,
    project=dbt_project,
    select=DOWNSTREAM_SELECT,
    exclude=PARTITIONED_SELECT,
    name="dbt_downstream",
    dagster_dbt_translator=translator,
    pool=DUCKDB_POOL,
)
def dbt_downstream_assets(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from _stream(dbt.cli(["build"], context=context))


DBT_ASSETS = [dbt_upstream_assets, dbt_partitioned_assets, dbt_downstream_assets]
