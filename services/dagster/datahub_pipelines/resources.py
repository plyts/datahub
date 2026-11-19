"""Dagster resources: DuckDB (analytical engine) and dbt Core CLI."""

from __future__ import annotations

import os
from pathlib import Path

from dagster_dbt import DbtCliResource, DbtProject
from dagster_duckdb import DuckDBResource

from datahub_pipelines.settings import get_settings

_settings = get_settings()

# A dbt test is only run by an op when all its parents are built by that op or are
# ancestors of them (avoids running cross-layer tests before their models exist).
os.environ.setdefault("DBT_INDIRECT_SELECTION", "buildable")

dbt_project = DbtProject(
    project_dir=Path(_settings.dbt_project_dir),
    profiles_dir=Path(_settings.dbt_project_dir),
    target=_settings.dbt_target,
)
# In `dagster dev`, (re)generate target/manifest.json on load. In Docker the manifest
# is generated at image build time (see Dockerfile), so this is a no-op there.
dbt_project.prepare_if_dev()


def ensure_duckdb_parent_dir(path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path


def build_resources() -> dict:
    return {
        "duckdb": DuckDBResource(database=ensure_duckdb_parent_dir(_settings.duckdb_path)),
        "dbt": DbtCliResource(
            project_dir=dbt_project, profiles_dir=_settings.dbt_project_dir, target=_settings.dbt_target
        ),
    }
