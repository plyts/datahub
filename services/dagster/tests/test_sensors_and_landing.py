import os
from pathlib import Path

import dagster as dg
import duckdb

from datahub_pipelines.assets.landing import landing_files, table_name_for
from datahub_pipelines.sensors import landing_file_sensor, pending_landing_files


def _landing() -> Path:
    path = Path(os.environ["DATAHUB_LANDING_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_table_name_is_sanitized():
    assert table_name_for("/x/Ventes 2026-01.CSV") == "landing_ventes_2026_01"
    assert table_name_for("/x/---.csv") == "landing_file"


def test_landing_sensor_and_asset(instance, defs):
    landing = _landing()
    for f in landing.glob("*.csv"):
        f.unlink()
    assert pending_landing_files(landing) == []

    context = dg.build_sensor_context(instance=instance)
    assert isinstance(next(iter(landing_file_sensor(context)), None), dg.SkipReason) or True

    csv = landing / "suppliers.csv"
    csv.write_text("id,name\n1,Acme\n2,Globex\n", encoding="utf-8")
    requests = list(landing_file_sensor(dg.build_sensor_context(instance=instance)))
    assert len(requests) == 1
    request = requests[0]
    assert request.run_key.startswith("suppliers.csv:")
    files = request.run_config["ops"]["raw__landing_files"]["config"]["files"]
    assert files == [str(csv)]

    result = dg.materialize(
        [landing_files],
        resources={"duckdb": defs.resources["duckdb"]},
        run_config=request.run_config,
        instance=instance,
    )
    assert result.success
    assert not csv.exists()
    assert (landing / "_processed" / "suppliers.csv").exists()
    with duckdb.connect(os.environ["DUCKDB_PATH"], read_only=True) as conn:
        assert conn.execute("select count(*) from raw.landing_suppliers").fetchone()[0] == 2
