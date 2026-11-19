"""Settings of the Data Hub UI, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: _env("DATAHUB_ENV", "dev"))
    version: str = field(default_factory=lambda: _env("DATAHUB_VERSION", "1.0.0"))
    git_sha: str = field(default_factory=lambda: _env("DATAHUB_GIT_SHA", "unknown"))
    public_url: str = field(default_factory=lambda: _env("DATAHUB_PUBLIC_URL", "http://localhost:8501").rstrip("/"))
    # "oidc" (Keycloak, mandatory outside dev) or "none" (local development only)
    auth_mode: str = field(default_factory=lambda: _env("DATAHUB_AUTH_MODE", "oidc"))
    dev_default_role: str = field(default_factory=lambda: _env("DATAHUB_DEV_ROLE", "ADMIN"))
    # Internal service URLs (Docker network)
    dagster_graphql_url: str = field(
        default_factory=lambda: _env("DAGSTER_GRAPHQL_URL", "http://dagster-webserver:3000/dagster/graphql")
    )
    dagster_location: str = field(default_factory=lambda: _env("DAGSTER_LOCATION", "datahub"))
    loki_url: str = field(default_factory=lambda: _env("LOKI_URL", "http://loki:3100"))
    prometheus_url: str = field(default_factory=lambda: _env("PROMETHEUS_URL", "http://prometheus:9090"))
    grafana_url: str = field(default_factory=lambda: _env("GRAFANA_INTERNAL_URL", "http://grafana:3000/grafana"))
    keycloak_url: str = field(default_factory=lambda: _env("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080/auth"))
    keycloak_realm: str = field(default_factory=lambda: _env("KEYCLOAK_REALM", "datahub"))
    keycloak_admin_client_id: str = field(default_factory=lambda: _env("KEYCLOAK_ADMIN_CLIENT_ID", "datahub-admin-api"))
    keycloak_admin_client_secret: str = field(default_factory=lambda: _env("KEYCLOAK_ADMIN_CLIENT_SECRET"))
    app_db_url: str = field(default_factory=lambda: _env("DATAHUB_APP_DB_URL"))
    dbt_artifacts_dir: str = field(default_factory=lambda: _env("DBT_ARTIFACTS_DIR", "/data/dbt_artifacts"))
    duckdb_path: str = field(default_factory=lambda: _env("DUCKDB_PATH", "/data/duckdb/datahub_dev.duckdb"))
    timezone: str = field(default_factory=lambda: _env("DATAHUB_TIMEZONE", "Europe/Paris"))

    @property
    def dagster_public_url(self) -> str:
        return f"{self.public_url}/dagster"

    @property
    def grafana_public_url(self) -> str:
        return f"{self.public_url}/grafana"

    @property
    def keycloak_public_url(self) -> str:
        return f"{self.public_url}/auth"

    @property
    def keycloak_account_url(self) -> str:
        return f"{self.keycloak_public_url}/realms/{self.keycloak_realm}/account"

    def validate(self) -> list[str]:
        """Return blocking configuration errors (shown instead of the app)."""
        errors = []
        if self.env not in ("dev", "staging", "prod"):
            errors.append(f"DATAHUB_ENV invalide : {self.env}")
        if self.auth_mode not in ("oidc", "none"):
            errors.append(f"DATAHUB_AUTH_MODE invalide : {self.auth_mode}")
        if self.auth_mode == "none" and self.env != "dev":
            errors.append("DATAHUB_AUTH_MODE=none est interdit hors de l'environnement dev.")
        return errors


def get_settings() -> Settings:
    return Settings()
