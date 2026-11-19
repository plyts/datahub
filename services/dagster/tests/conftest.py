"""Test configuration.

Settings are read at import time by the code location, so the environment must be
prepared *before* `datahub_pipelines` is imported: every test session gets its own
temporary DuckDB file, artifacts and landing directories.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="datahub-tests-"))
os.environ.update(
    {
        "DATAHUB_ENV": "dev",
        "DUCKDB_PATH": str(_TMP / "datahub_test.duckdb"),
        "DBT_ARTIFACTS_DIR": str(_TMP / "dbt_artifacts"),
        "DATAHUB_LANDING_DIR": str(_TMP / "landing"),
        "DATAHUB_PARTITIONS_START": "2026-01-01",
        "DATAHUB_APP_DB_RO_URL": "",
        "NOTIFY_WEBHOOK_URL": "",
        "SMTP_HOST": "",
    }
)

import subprocess  # noqa: E402
import sys  # noqa: E402

import dagster as dg  # noqa: E402
import pytest  # noqa: E402

DBT_PROJECT = Path(__file__).resolve().parents[1] / "dbt_project"


def pytest_sessionstart(session):
    """Always test against a manifest generated from the current dbt sources (never a stale one)."""
    subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "parse", "--profiles-dir", str(DBT_PROJECT), "--project-dir",
         str(DBT_PROJECT), "--quiet"],
        check=True,
        env={**os.environ, "DUCKDB_PATH": str(_TMP / "parse.duckdb")},
    )


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return _TMP


@pytest.fixture(scope="session")
def defs():
    from datahub_pipelines.definitions import defs as definitions

    return definitions


@pytest.fixture(scope="session")
def instance():
    with dg.instance_for_test() as inst:
        yield inst
