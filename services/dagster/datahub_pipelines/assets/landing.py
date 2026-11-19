"""Event-driven ingestion of CSV files dropped in the landing directory.

The `landing_file_sensor` (see sensors.py) launches one run per new file.
Each file `<name>.csv` is loaded into `raw.landing_<name>` then moved to `_processed/`.
"""

import re
import shutil
from pathlib import Path

import dagster as dg
from dagster_duckdb import DuckDBResource
from pydantic import Field

from datahub_pipelines.partitions import DUCKDB_POOL

_SAFE = re.compile(r"[^a-z0-9_]")


def table_name_for(path: str | Path) -> str:
    stem = _SAFE.sub("_", Path(path).stem.lower()).strip("_") or "file"
    return f"landing_{stem}"[:63]


class LandingFilesConfig(dg.Config):
    files: list[str] = Field(default_factory=list, description="Chemins absolus des fichiers CSV à charger.")


@dg.asset(
    key=["raw", "landing_files"],
    group_name="ingestion",
    kinds={"python", "duckdb", "csv"},
    owners=["team:data-engineering"],
    pool=DUCKDB_POOL,
    description="Fichiers CSV déposés dans le répertoire d'atterrissage (chargés par le sensor).",
    tags={"layer": "raw", "trigger": "sensor"},
)
def landing_files(
    context: dg.AssetExecutionContext, config: LandingFilesConfig, duckdb: DuckDBResource
) -> dg.MaterializeResult:
    loaded: dict[str, int] = {}
    with duckdb.get_connection() as conn:
        conn.execute("create schema if not exists raw")
        for file in config.files:
            path = Path(file)
            if not path.exists():
                context.log.warning("File %s disappeared, skipping", path)
                continue
            table = table_name_for(path)
            conn.execute(
                f"create or replace table raw.{table} as select * from read_csv_auto(?, header = true)", [str(path)]
            )
            rows = conn.execute(f"select count(*) from raw.{table}").fetchone()[0]
            loaded[table] = rows
            processed = path.parent / "_processed"
            processed.mkdir(exist_ok=True)
            shutil.move(str(path), processed / path.name)
            context.log.info("Loaded %s rows from %s into raw.%s", rows, path.name, table)
    return dg.MaterializeResult(
        metadata={"dagster/row_count": sum(loaded.values()), "tables": dg.MetadataValue.json(loaded)}
    )
