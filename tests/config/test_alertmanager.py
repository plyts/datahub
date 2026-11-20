"""Alertmanager: configuration rendered from the notification settings, and its wiring.

The renderer is run with `sh` (dash on Ubuntu, POSIX like the busybox shell of the image).
`amtool check-config` validates the rendered file in CI (job test-config).
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "services" / "alertmanager" / "render-config.sh"
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
PROMETHEUS = yaml.safe_load((ROOT / "services" / "prometheus" / "prometheus.yml").read_text())


def render(tmp_path: Path, **env) -> tuple[dict, str, Path]:
    out = tmp_path / "am"
    base = {"PATH": os.environ["PATH"], "DATAHUB_ENV": "prod"}
    result = subprocess.run(
        ["sh", str(SCRIPT), str(out)], env={**base, **env}, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    text = (out / "alertmanager.yml").read_text()
    return yaml.safe_load(text), text, out


def receiver(config: dict, name: str) -> dict:
    return next(r for r in config["receivers"] if r["name"] == name)


def test_nothing_configured_routes_to_the_null_receiver(tmp_path):
    config, _, out = render(tmp_path)
    assert config["route"]["receiver"] == "null"
    assert set(receiver(config, "notify")) == {"name"}
    assert not (out / "webhook_url").exists()


def test_webhook_url_is_kept_out_of_the_yaml(tmp_path):
    url = "https://chat.example.com/hooks/s3cr3t"
    config, text, out = render(tmp_path, NOTIFY_WEBHOOK_URL=url)
    assert config["route"]["receiver"] == "notify"
    slack = receiver(config, "notify")["slack_configs"][0]
    assert slack["api_url_file"] == str(out / "webhook_url")
    assert (out / "webhook_url").read_text() == url
    assert "s3cr3t" not in text
    assert stat.S_IMODE((out / "webhook_url").stat().st_mode) == 0o600
    assert slack["send_resolved"] is True
    assert slack["title"].startswith("[Data Hub PROD]")


def test_email_needs_a_relay_and_a_recipient(tmp_path):
    config, _, _ = render(tmp_path, SMTP_HOST="smtp.example.com", NOTIFY_EMAIL_TO="ops@example.com")
    email = receiver(config, "notify")["email_configs"][0]
    assert config["route"]["receiver"] == "notify"
    assert email["to"] == "ops@example.com"
    assert email["smarthost"] == "smtp.example.com:25"
    assert email["from"] == "datahub@localhost"
    assert email["require_tls"] is False

    config, _, _ = render(tmp_path / "relay-only", SMTP_HOST="smtp.example.com")
    assert config["route"]["receiver"] == "null"


def test_values_are_quoted_safely(tmp_path):
    sender = 'Data "Hub" \\ alerts <datahub@example.com>'
    config, _, _ = render(
        tmp_path, SMTP_HOST="smtp.example.com", SMTP_PORT="2525", SMTP_FROM=sender, NOTIFY_EMAIL_TO="a@x.fr, b@x.fr"
    )
    email = receiver(config, "notify")["email_configs"][0]
    assert email["from"] == sender
    assert email["to"] == "a@x.fr, b@x.fr"
    assert email["smarthost"] == "smtp.example.com:2525"


def test_both_channels_and_critical_inhibits_warning(tmp_path):
    config, _, _ = render(
        tmp_path, NOTIFY_WEBHOOK_URL="https://hooks.example.com/x", SMTP_HOST="relay", NOTIFY_EMAIL_TO="ops@x.fr"
    )
    notify = receiver(config, "notify")
    assert {"slack_configs", "email_configs"} <= set(notify)
    inhibit = config["inhibit_rules"][0]
    assert inhibit["source_matchers"] == ['severity="critical"']
    assert inhibit["target_matchers"] == ['severity="warning"']


def test_every_alert_rule_has_a_severity_and_a_summary():
    rules = yaml.safe_load((ROOT / "services" / "prometheus" / "rules" / "datahub.yml").read_text())
    for group in rules["groups"]:
        for rule in group["rules"]:
            assert rule["labels"]["severity"] in {"critical", "warning"}, rule["alert"]
            assert rule["annotations"]["summary"], rule["alert"]


def test_prometheus_sends_alerts_to_alertmanager_and_scrapes_it():
    targets = PROMETHEUS["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]
    assert targets == ["alertmanager:9093"]
    jobs = {job["job_name"]: job for job in PROMETHEUS["scrape_configs"]}
    assert jobs["alertmanager"]["static_configs"][0]["targets"] == ["alertmanager:9093"]


@pytest.mark.parametrize("variable", ["NOTIFY_WEBHOOK_URL", "SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "NOTIFY_EMAIL_TO"])
def test_alertmanager_service_gets_the_notification_settings(variable):
    service = COMPOSE["services"]["alertmanager"]
    assert variable in service["environment"]
    assert service["entrypoint"][:2] == ["/bin/sh", "/etc/alertmanager/render-config.sh"]
    assert "ports" not in service, "reachable on the internal network only"


def test_grafana_shows_the_alertmanager():
    datasources = yaml.safe_load((ROOT / "services/grafana/provisioning/datasources/datasources.yml").read_text())
    alertmanager = next(d for d in datasources["datasources"] if d["type"] == "alertmanager")
    assert alertmanager["url"] == "http://alertmanager:9093"
    assert alertmanager["jsonData"]["implementation"] == "prometheus"
