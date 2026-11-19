"""Static jobs of the Data Hub."""

from __future__ import annotations

import dagster as dg

from datahub_pipelines.assets.dbt import dbt_downstream_assets, dbt_upstream_assets
from datahub_pipelines.assets.ingestion import raw_customers
from datahub_pipelines.assets.landing import landing_files
from datahub_pipelines.ops.docs import dbt_docs_job

# Everything except the event-driven landing files: reference data, the day's
# partition of raw data, then all dbt layers in dependency order.
daily_partition_job = dg.define_asset_job(
    name="daily_partition_job",
    description="Pipeline quotidien : ingestion de la partition du jour + transformations dbt + tests.",
    selection=dg.AssetSelection.all() - dg.AssetSelection.assets(landing_files),
    tags={"datahub/kind": "pipeline", "datahub/criticality": "high"},
)

dbt_marts_job = dg.define_asset_job(
    name="dbt_marts_job",
    description="Reconstruction des couches dbt non partitionnées (staging, intermediate, marts, référentiels).",
    selection=dg.AssetSelection.assets(raw_customers, dbt_upstream_assets, dbt_downstream_assets),
    tags={"datahub/kind": "pipeline"},
)

landing_files_job = dg.define_asset_job(
    name="landing_files_job",
    description="Chargement des fichiers CSV déposés dans le répertoire d'atterrissage.",
    selection=dg.AssetSelection.assets(landing_files),
    tags={"datahub/kind": "event"},
)

STATIC_JOBS = [daily_partition_job, dbt_marts_job, landing_files_job, dbt_docs_job]
