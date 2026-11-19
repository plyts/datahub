"""Generate dbt documentation artifacts (manifest, catalog, docs site) for the Data Catalog."""

import shutil
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource

from datahub_pipelines.partitions import DUCKDB_POOL
from datahub_pipelines.settings import get_settings

ARTIFACTS = ("manifest.json", "catalog.json", "run_results.json", "index.html", "semantic_manifest.json")


def publish_artifacts(source_dir: Path, dest_dir: Path) -> list[str]:
    """Atomically copy dbt artifacts into the shared directory read by the Data Hub UI."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ARTIFACTS:
        src = source_dir / name
        if src.exists():
            tmp = dest_dir / f".{name}.tmp"
            shutil.copyfile(src, tmp)
            tmp.replace(dest_dir / name)
            copied.append(name)
    return copied


@dg.op(pool=DUCKDB_POOL, description="dbt docs generate + publication des artefacts pour le Data Catalog.")
def dbt_docs_generate(context: dg.OpExecutionContext, dbt: DbtCliResource) -> None:
    invocation = dbt.cli(["docs", "generate"], raise_on_error=True).wait()
    copied = publish_artifacts(invocation.target_path, Path(get_settings().dbt_artifacts_dir))
    context.log.info("Published dbt artifacts %s to %s", copied, get_settings().dbt_artifacts_dir)


@dg.job(
    description="Génère la documentation dbt et publie les artefacts (catalogue, lineage).",
    tags={"datahub/kind": "docs"},
)
def dbt_docs_job():
    dbt_docs_generate()
