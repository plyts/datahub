"""Ingestion assets: load raw data into the DuckDB `raw` schema.

Each partitioned asset deletes then re-inserts its partitions, so any re-run,
retry or backfill is idempotent.
"""

from datetime import UTC, date, datetime

import dagster as dg
import pandas as pd
from dagster_duckdb import DuckDBResource
from pydantic import Field

from datahub_pipelines import generators
from datahub_pipelines.freshness import RAW_DAILY
from datahub_pipelines.partitions import DUCKDB_POOL, daily_partitions
from datahub_pipelines.settings import get_settings

RAW_DDL = {
    "customers": """
        create table if not exists raw.customers (
            customer_id integer, first_name varchar, last_name varchar, email varchar,
            country_code varchar, created_at timestamp, _loaded_at timestamp
        )""",
    "orders": """
        create table if not exists raw.orders (
            order_id varchar, customer_id integer, order_date date, status varchar,
            product_category varchar, amount decimal(12, 2), _partition_date date, _loaded_at timestamp
        )""",
    "payments": """
        create table if not exists raw.payments (
            payment_id varchar, order_id varchar, payment_method varchar, amount decimal(12, 2),
            paid_at timestamp, _partition_date date, _loaded_at timestamp
        )""",
}

# Transient errors (locked file, I/O) are retried with exponential backoff.
INGESTION_RETRY = dg.RetryPolicy(max_retries=3, delay=5, backoff=dg.Backoff.EXPONENTIAL, jitter=dg.Jitter.PLUS_MINUS)
COMMON = {
    "group_name": "ingestion",
    "kinds": {"python", "duckdb"},
    "owners": ["team:data-engineering"],
    "retry_policy": INGESTION_RETRY,
    "pool": DUCKDB_POOL,
}


class SimulatedSourceConfig(dg.Config):
    """Test scenarios: inject a fault in the simulated source (refused in production)."""

    fault: str = Field(default="none", description="Panne simulée : " + ", ".join(generators.FAULTS))


def resolve_fault(config: SimulatedSourceConfig, applicable: set[str]) -> str:
    fault = config.fault or "none"
    if fault not in generators.FAULTS:
        raise dg.Failure(f"Panne simulée inconnue : {fault!r}. Valeurs possibles : {', '.join(generators.FAULTS)}")
    if fault != "none" and get_settings().env == "prod":
        raise dg.Failure("L'injection de pannes est interdite en production.")
    return fault if fault in applicable else "none"


def ensure_raw_tables(conn) -> None:
    conn.execute("create schema if not exists raw")
    for ddl in RAW_DDL.values():
        conn.execute(ddl)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def replace_partition(conn, table: str, partition: date, df: pd.DataFrame) -> int:
    """Idempotent write: delete the partition then insert the new rows."""
    df = df.copy()
    df["_partition_date"] = partition
    df["_loaded_at"] = _now()
    conn.execute(f"delete from raw.{table} where _partition_date = ?", [partition])
    if not df.empty:
        conn.register("_incoming", df)
        try:
            conn.execute(f"insert into raw.{table} by name select * from _incoming")
        finally:
            conn.unregister("_incoming")
    return len(df)


def _partition_dates(context: dg.AssetExecutionContext) -> list[date]:
    return [date.fromisoformat(key) for key in context.partition_keys]


@dg.asset(
    key=["raw", "customers"],
    freshness_policy=RAW_DAILY,
    description="Référentiel clients (rechargé intégralement à chaque exécution).",
    tags={"layer": "raw", "domain": "customers"},
    **COMMON,
)
def raw_customers(
    context: dg.AssetExecutionContext, config: SimulatedSourceConfig, duckdb: DuckDBResource
) -> dg.MaterializeResult:
    fault = resolve_fault(config, generators.CUSTOMER_FAULTS)
    if fault != "none":
        context.log.warning("Simulated fault injected: %s", generators.FAULTS[fault])
    df = generators.customers_with_fault(fault)
    df["_loaded_at"] = _now()
    with duckdb.get_connection() as conn:
        ensure_raw_tables(conn)
        conn.execute("delete from raw.customers")
        conn.register("_incoming", df)
        try:
            conn.execute("insert into raw.customers by name select * from _incoming")
        finally:
            conn.unregister("_incoming")
    context.log.info("Loaded %s customers", len(df))
    return dg.MaterializeResult(metadata={"dagster/row_count": len(df), "table": "raw.customers"})


@dg.asset(
    key=["raw", "orders"],
    freshness_policy=RAW_DAILY,
    description="Commandes brutes, partitionnées par jour.",
    partitions_def=daily_partitions,
    backfill_policy=dg.BackfillPolicy.single_run(),
    tags={"layer": "raw", "domain": "sales"},
    **COMMON,
)
def raw_orders(
    context: dg.AssetExecutionContext, config: SimulatedSourceConfig, duckdb: DuckDBResource
) -> dg.MaterializeResult:
    fault = resolve_fault(config, generators.ORDER_FAULTS)
    if fault != "none":
        context.log.warning("Simulated fault injected: %s", generators.FAULTS[fault])
    # Transient errors of the source are retried by INGESTION_RETRY (exponential backoff).
    generators.check_source(fault, context.retry_number)
    total = 0
    days = _partition_dates(context)
    with duckdb.get_connection() as conn:
        ensure_raw_tables(conn)
        for day in days:
            total += replace_partition(conn, "orders", day, generators.orders_with_fault(day, fault))
    context.log.info("Loaded %s orders for %s partition(s)", total, len(days))
    return dg.MaterializeResult(metadata={"dagster/row_count": total, "partitions": len(days), "table": "raw.orders"})


@dg.asset(
    key=["raw", "payments"],
    freshness_policy=RAW_DAILY,
    deps=[raw_orders],
    description="Paiements bruts, partitionnés par jour (dérivés des commandes du jour).",
    partitions_def=daily_partitions,
    backfill_policy=dg.BackfillPolicy.single_run(),
    tags={"layer": "raw", "domain": "sales"},
    **COMMON,
)
def raw_payments(context: dg.AssetExecutionContext, duckdb: DuckDBResource) -> dg.MaterializeResult:
    total = 0
    days = _partition_dates(context)
    with duckdb.get_connection() as conn:
        ensure_raw_tables(conn)
        for day in days:
            total += replace_partition(conn, "payments", day, generators.generate_payments(day))
    context.log.info("Loaded %s payments for %s partition(s)", total, len(days))
    return dg.MaterializeResult(metadata={"dagster/row_count": total, "partitions": len(days), "table": "raw.payments"})
