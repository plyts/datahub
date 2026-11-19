"""Static checks of docker-compose.yml (security, reproducibility, resources)."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]
VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::?[-?][^}]*)?\}")


def declared_variables() -> set[str]:
    names = set()
    for path in [ROOT / ".env.example", *sorted((ROOT / "envs").glob("*.env"))]:
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                names.add(line.split("=", 1)[0].strip())
    return names


def test_expected_services():
    expected = {"postgres", "keycloak", "dagster-code", "dagster-webserver", "dagster-daemon", "datahub-ui",
                "datahub-exporter", "caddy", "oauth2-proxy", "prometheus", "grafana", "loki", "alloy",
                "node-exporter", "cadvisor", "postgres-exporter"}
    assert set(SERVICES) == expected


def test_only_caddy_publishes_ports():
    assert [name for name, svc in SERVICES.items() if svc.get("ports")] == ["caddy"]


@pytest.mark.parametrize("name", list(SERVICES))
def test_images_are_pinned(name):
    image = SERVICES[name]["image"]
    assert ":" in image and not image.endswith(":latest"), image


@pytest.mark.parametrize("name", list(SERVICES))
def test_memory_limit_and_log_rotation(name):
    assert SERVICES[name].get("mem_limit"), f"{name} has no mem_limit"
    assert SERVICES[name]["logging"]["options"]["max-size"]


def test_total_memory_fits_the_vm():
    def to_mb(value: str) -> float:
        number, unit = float(value[:-1]), value[-1].lower()
        return number * 1024 if unit == "g" else number

    total_gb = sum(to_mb(s["mem_limit"]) for s in SERVICES.values()) / 1024
    assert total_gb <= 14, f"{total_gb:.1f} GB exceeds the 12-16 GB VM sizing"


@pytest.mark.parametrize("name", ["postgres", "keycloak", "dagster-code", "dagster-webserver", "grafana",
                                  "datahub-exporter"])
def test_critical_services_have_healthchecks(name):
    assert SERVICES[name].get("healthcheck")


def test_every_variable_is_declared():
    text = (ROOT / "docker-compose.yml").read_text()
    used = set(VAR_RE.findall(text))
    missing = used - declared_variables() - {"DATAHUB_GIT_SHA"}
    assert not missing, f"variables used in docker-compose.yml but not declared: {sorted(missing)}"


def test_secrets_are_required_not_defaulted():
    text = (ROOT / "docker-compose.yml").read_text()
    for name in ("POSTGRES_PASSWORD", "OIDC_CLIENT_SECRET", "KEYCLOAK_ADMIN_PASSWORD", "STREAMLIT_COOKIE_SECRET"):
        assert re.search(r"\$\{" + name + r":\?\}", text), f"{name} must be mandatory (${{VAR:?}})"


def test_dagster_services_share_storage_and_image():
    images = {SERVICES[s]["image"] for s in ("dagster-code", "dagster-webserver", "dagster-daemon")}
    assert len(images) == 1
    for s in ("dagster-code", "dagster-webserver", "dagster-daemon"):
        assert any("dagster_storage:/opt/dagster/storage" in v for v in SERVICES[s]["volumes"])


def test_ui_mounts_data_read_only():
    assert all(v.endswith(":ro") for v in SERVICES["datahub-ui"]["volumes"])


def test_public_hostname_resolves_to_caddy_inside_the_network():
    assert SERVICES["caddy"]["networks"]["default"]["aliases"] == ["${DATAHUB_HOST:?}"]


def test_oauth2_proxy_restricts_dagster_to_engineers():
    assert SERVICES["oauth2-proxy"]["environment"]["OAUTH2_PROXY_ALLOWED_ROLES"] == "ADMIN,DATA_ENGINEER"


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_environment_files(env):
    values = dict(line.split("=", 1) for line in (ROOT / "envs" / f"{env}.env").read_text().splitlines()
                  if "=" in line and not line.startswith("#"))
    assert values["DATAHUB_ENV"] == env
    assert values["DATAHUB_PUBLIC_URL"].endswith(values["DATAHUB_HOST"])
    assert values["DATAHUB_AUTH_MODE"] == "oidc"
    assert (ROOT / "compose" / f"docker-compose.{env}.yml").exists()
    if env == "prod":
        assert values["COOKIE_SECURE"] == "true" and values["DATAHUB_PUBLIC_URL"].startswith("https://")


def test_locally_built_images_are_never_pulled():
    built = {svc["image"] for svc in SERVICES.values() if "build" in svc}
    for name, svc in SERVICES.items():
        if svc["image"] in built and "build" not in svc:
            assert svc.get("pull_policy") == "never", name
