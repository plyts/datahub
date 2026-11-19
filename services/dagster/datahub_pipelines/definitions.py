"""Entry point of the `datahub` code location: `dagster code-server start -m datahub_pipelines.definitions`."""

from __future__ import annotations

import dagster as dg

from datahub_pipelines.assets.dbt import DBT_ASSETS
from datahub_pipelines.assets.ingestion import raw_customers, raw_orders, raw_payments
from datahub_pipelines.assets.landing import landing_files
from datahub_pipelines.checks import ASSET_CHECKS
from datahub_pipelines.custom_pipelines import build_custom_pipelines, load_specs_from_db
from datahub_pipelines.jobs import STATIC_JOBS
from datahub_pipelines.resources import build_resources
from datahub_pipelines.schedules import STATIC_SCHEDULES
from datahub_pipelines.sensors import STATIC_SENSORS
from datahub_pipelines.settings import get_settings

settings = get_settings()

ALL_ASSETS = [raw_customers, raw_orders, raw_payments, landing_files, *DBT_ASSETS]
RESOURCES = build_resources()

custom = build_custom_pipelines(load_specs_from_db(settings.app_db_url), ALL_ASSETS, DBT_ASSETS, RESOURCES)

defs = dg.Definitions(
    assets=ALL_ASSETS,
    asset_checks=ASSET_CHECKS,
    jobs=[*STATIC_JOBS, *custom.jobs],
    schedules=[*STATIC_SCHEDULES, *custom.schedules],
    sensors=STATIC_SENSORS,
    resources=RESOURCES,
    # DuckDB has a single writer: steps of a run are executed sequentially in one process.
    executor=dg.in_process_executor,
    metadata={
        "datahub/env": settings.env,
        "datahub/custom_pipelines": len(custom.jobs),
        "datahub/custom_pipeline_errors": dg.MetadataValue.json(custom.errors),
    },
)
