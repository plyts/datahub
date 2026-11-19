"""Render every page with Streamlit's AppTest, for every role, against the fake backends."""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from datahub_ui.auth import dev_user
from datahub_ui.navigation import PAGES, visible_pages
from datahub_ui.rbac import Role

HARNESS = Path(__file__).parent / "page_harness.py"


@pytest.fixture(autouse=True)
def fake_env(monkeypatch, artifacts_dir):
    monkeypatch.setenv("DATAHUB_UI_FAKE_BACKENDS", "1")
    monkeypatch.setenv("DATAHUB_AUTH_MODE", "none")
    monkeypatch.setenv("DATAHUB_ENV", "dev")
    monkeypatch.setenv("DBT_ARTIFACTS_DIR", str(artifacts_dir))
    monkeypatch.setenv("DUCKDB_PATH", "/nonexistent/datahub.duckdb")


def run_page(module: str, role: str) -> AppTest:
    os.environ["HARNESS_PAGE"] = module
    os.environ["HARNESS_ROLE"] = role
    at = AppTest.from_file(str(HARNESS), default_timeout=60)
    at.run()
    return at


CASES = [(p.module, r.value) for r in Role for p in visible_pages(dev_user(r.value))]


@pytest.mark.parametrize(("module", "role"), CASES)
def test_page_renders_without_exception(module, role):
    at = run_page(module, role)
    assert not at.exception, [e.value for e in at.exception]
    assert at.title, "every page has a title"


def test_all_pages_covered_for_admin():
    assert {m for m, r in CASES if r == "ADMIN"} == {p.module for p in PAGES}


def test_viewer_cannot_launch_pipelines():
    at = run_page("m01_pipelines", "VIEWER")
    assert not [b for b in at.button if b.key == "p01_launch"]
    assert any("pipelines.run" in i.value for i in at.info)


def test_engineer_launches_a_run_and_it_is_audited():
    from datahub_ui.testing.fakes import fake_clients

    at = run_page("m01_pipelines", "DATA_ENGINEER")
    clients = fake_clients()
    before = len(clients["dagster"].launched)
    at.button(key="p01_launch").click().run()
    assert not at.exception
    assert len(clients["dagster"].launched) == before + 1
    assert clients["db"].audit_rows[0]["action"] == "pipeline.launch"
    assert clients["db"].audit_rows[0]["username"] == "dev-data_engineer"
    assert any("Run lancé" in s.value for s in at.success)


def test_failed_runs_become_classified_incidents():
    from datahub_ui.testing.fakes import fake_clients

    at = run_page("m06_logs", "DATA_ENGINEER")
    assert not at.exception
    incidents = fake_clients()["db"].list_incidents()
    assert incidents and incidents[0]["category"] == "Erreur SQL / dbt"


def test_engineer_launches_a_fault_scenario():
    from datahub_ui.testing.fakes import fake_clients

    at = run_page("m01_pipelines", "DATA_ENGINEER")
    at.selectbox(key="p01_fault").set_value("negative_amounts").run()
    at.button(key="p01_launch").click().run()
    assert not at.exception
    run = fake_clients()["dagster"].launched[0]
    assert run["run_config"] == {"ops": {"raw__orders": {"config": {"fault": "negative_amounts"}}}}
    assert run["tags"]["datahub/scenario"] == "negative_amounts"
    assert fake_clients()["db"].audit_rows[0]["details"]["scenario"] == "negative_amounts"


def test_fault_scenarios_are_hidden_in_production(monkeypatch):
    monkeypatch.setenv("DATAHUB_ENV", "prod")
    at = run_page("m01_pipelines", "DATA_ENGINEER")
    assert not at.exception
    assert not [s for s in at.selectbox if s.key == "p01_fault"]
