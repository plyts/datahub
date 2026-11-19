"""The UI offers exactly the faults the Dagster simulated source implements."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "datahub-ui"))
from datahub_ui.scenarios import CUSTOMER_FAULTS as UI_CUSTOMER_FAULTS  # noqa: E402
from datahub_ui.scenarios import FAULTS as UI_FAULTS  # noqa: E402


def dagster_constant(name: str):
    tree = ast.parse((ROOT / "services/dagster/datahub_pipelines/generators.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def test_fault_catalogs_match():
    assert dagster_constant("FAULTS") == UI_FAULTS
    assert dagster_constant("CUSTOMER_FAULTS") == UI_CUSTOMER_FAULTS
