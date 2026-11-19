"""Partition definitions shared by ingestion assets and incremental dbt models."""

from __future__ import annotations

import dagster as dg

from datahub_pipelines.settings import get_settings

_settings = get_settings()

daily_partitions = dg.DailyPartitionsDefinition(
    start_date=_settings.partitions_start,
    timezone=_settings.timezone,
    # The partition for day D becomes available once day D is over.
    end_offset=0,
)

# DuckDB allows a single writer process per database file: every op that writes to
# DuckDB is placed in this pool, whose limit is set to 1 in dagster.yaml.
DUCKDB_POOL = "duckdb"
