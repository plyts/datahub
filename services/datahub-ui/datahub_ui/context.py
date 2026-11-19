"""Application context: settings, current user and backend clients, shared by all pages."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import streamlit as st

from datahub_ui.auth import User
from datahub_ui.clients.dagster import DagsterClient
from datahub_ui.clients.dbt_artifacts import DbtArtifacts
from datahub_ui.clients.keycloak import KeycloakAdmin
from datahub_ui.clients.observability import LokiClient, PrometheusClient
from datahub_ui.config import Settings
from datahub_ui.db.repository import AppDB
from datahub_ui.rbac import Perm

log = logging.getLogger("datahub.ui")


@dataclass
class AppContext:
    settings: Settings
    user: User
    dagster: Any
    db: Any
    loki: Any
    prometheus: Any
    keycloak: Any

    def artifacts(self) -> DbtArtifacts:
        return _load_artifacts(self.settings.dbt_artifacts_dir, _artifacts_mtime(self.settings.dbt_artifacts_dir))

    def audit(self, action: str, target: str = "", details: dict | None = None, success: bool = True) -> None:
        """Record a user action in the audit trail (and in the container logs, collected by Loki)."""
        log.info(
            "AUDIT user=%s action=%s target=%s success=%s details=%s",
            self.user.username,
            action,
            target,
            success,
            details or {},
        )
        if self.db is None:
            return
        try:
            self.db.audit(
                self.user.username, sorted(r.value for r in self.user.roles), action, target, details, success
            )
        except Exception as exc:  # noqa: BLE001 - audit storage failure is reported, never silent
            log.error("Audit log write failed: %s", exc)
            st.warning("⚠️ L'action n'a pas pu être enregistrée dans le journal d'audit.")

    def allowed(self, perm: Perm) -> bool:
        return self.user.can(perm)


def _artifacts_mtime(directory: str) -> float:
    path = os.path.join(directory, "manifest.json")
    return os.path.getmtime(path) if os.path.exists(path) else 0.0


@st.cache_data(show_spinner=False)
def _load_artifacts(directory: str, _mtime: float) -> DbtArtifacts:  # cache invalidated when the files change
    return DbtArtifacts.load(directory)


@st.cache_resource(show_spinner=False)
def _real_clients(settings: Settings) -> dict[str, Any]:
    return {
        "dagster": DagsterClient(settings.dagster_graphql_url, location=settings.dagster_location),
        "db": AppDB(settings.app_db_url) if settings.app_db_url else None,
        "loki": LokiClient(settings.loki_url),
        "prometheus": PrometheusClient(settings.prometheus_url),
        "keycloak": KeycloakAdmin(
            settings.keycloak_url,
            settings.keycloak_realm,
            settings.keycloak_admin_client_id,
            settings.keycloak_admin_client_secret,
        ),
    }


def build_context(settings: Settings, user: User) -> AppContext:
    if os.getenv("DATAHUB_UI_FAKE_BACKENDS") == "1":
        from datahub_ui.testing.fakes import fake_clients

        clients = fake_clients()
    else:
        clients = _real_clients(settings)
    return AppContext(settings=settings, user=user, **clients)
