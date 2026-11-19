import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

os.environ.setdefault("DATAHUB_ENV", "dev")
os.environ.setdefault("DATAHUB_AUTH_MODE", "none")
os.environ.setdefault("DBT_ARTIFACTS_DIR", str(FIXTURES / "dbt_artifacts"))


@pytest.fixture
def artifacts_dir() -> Path:
    return FIXTURES / "dbt_artifacts"
