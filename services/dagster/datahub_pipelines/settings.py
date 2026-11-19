"""Runtime settings, read once from environment variables.

Every value has a safe default for local development so that
`dagster dev -m datahub_pipelines.definitions` works without any setup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SERVICE_DIR = PACKAGE_DIR.parent
DEFAULT_DBT_PROJECT_DIR = SERVICE_DIR / "dbt_project"

VALID_ENVS = ("dev", "staging", "prod")


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: _env("DATAHUB_ENV", "dev"))
    timezone: str = field(default_factory=lambda: _env("DATAHUB_TIMEZONE", "Europe/Paris"))
    partitions_start: str = field(default_factory=lambda: _env("DATAHUB_PARTITIONS_START", "2026-01-01"))
    duckdb_path: str = field(
        default_factory=lambda: _env("DUCKDB_PATH", str(SERVICE_DIR / "data" / "datahub_dev.duckdb"))
    )
    dbt_project_dir: str = field(default_factory=lambda: _env("DBT_PROJECT_DIR", str(DEFAULT_DBT_PROJECT_DIR)))
    dbt_target: str = field(default_factory=lambda: _env("DBT_TARGET", _env("DATAHUB_ENV", "dev")))
    dbt_artifacts_dir: str = field(
        default_factory=lambda: _env("DBT_ARTIFACTS_DIR", str(SERVICE_DIR / "data" / "dbt_artifacts"))
    )
    landing_dir: str = field(default_factory=lambda: _env("DATAHUB_LANDING_DIR", str(SERVICE_DIR / "data" / "landing")))
    # Read-only connection to the Data Hub application database (custom pipelines). Empty = disabled.
    app_db_url: str = field(default_factory=lambda: _env("DATAHUB_APP_DB_RO_URL", ""))
    public_url: str = field(default_factory=lambda: _env("DATAHUB_PUBLIC_URL", "http://localhost"))
    notify_webhook_url: str = field(default_factory=lambda: _env("NOTIFY_WEBHOOK_URL", ""))
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "25")))
    smtp_from: str = field(default_factory=lambda: _env("SMTP_FROM", "datahub@localhost"))
    smtp_to: str = field(default_factory=lambda: _env("NOTIFY_EMAIL_TO", ""))

    def __post_init__(self) -> None:
        if self.env not in VALID_ENVS:
            raise ValueError(f"DATAHUB_ENV must be one of {VALID_ENVS}, got {self.env!r}")


def get_settings() -> Settings:
    return Settings()
