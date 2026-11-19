"""Grafana dashboards are generated from code and reference provisioned datasources only."""

import json
import runpy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAFANA = ROOT / "services/grafana"


def test_dashboards_are_in_sync_with_generator():
    rendered = runpy.run_path(str(GRAFANA / "build_dashboards.py"))["render"]()
    for name, content in rendered.items():
        assert (GRAFANA / "dashboards" / name).read_text() == content, f"{name} out of date: run make dashboards"


def test_dashboards_use_provisioned_datasources():
    uids = {d["uid"] for d in yaml.safe_load((GRAFANA / "provisioning/datasources/datasources.yml").read_text())["datasources"]}
    for path in (GRAFANA / "dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text())
        for panel in dashboard["panels"]:
            assert panel["datasource"]["uid"] in uids
            for target in panel["targets"]:
                assert target["datasource"]["uid"] in uids


def test_prometheus_scrapes_every_exporter():
    config = yaml.safe_load((ROOT / "services/prometheus/prometheus.yml").read_text())
    jobs = {job["job_name"] for job in config["scrape_configs"]}
    assert {"dagster", "node", "cadvisor", "postgres", "keycloak", "caddy", "oauth2-proxy", "loki", "alloy"} <= jobs


def test_alert_rules_have_severity_and_summary():
    rules = yaml.safe_load((ROOT / "services/prometheus/rules/datahub.yml").read_text())
    for group in rules["groups"]:
        for rule in group["rules"]:
            assert rule["labels"]["severity"] in ("warning", "critical")
            assert rule["annotations"]["summary"]
