"""Role-based access control.

Keycloak owns identities and roles (realm roles: ADMIN, DATA_ENGINEER, ANALYST, VIEWER).
This module maps roles to permissions. Every sensitive action is checked on the
server side (Streamlit runs on the server) with `require()` right before the call
to the backend, and recorded in the audit log.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    DATA_ENGINEER = "DATA_ENGINEER"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"


class Perm(StrEnum):
    DASHBOARD_VIEW = "dashboard.view"
    PIPELINES_VIEW = "pipelines.view"
    PIPELINES_RUN = "pipelines.run"
    PIPELINES_EDIT = "pipelines.edit"
    SCHEDULES_VIEW = "schedules.view"
    SCHEDULES_EDIT = "schedules.edit"
    LINEAGE_VIEW = "lineage.view"
    CATALOG_VIEW = "catalog.view"
    CATALOG_EDIT = "catalog.edit"
    QUALITY_VIEW = "quality.view"
    LOGS_VIEW = "logs.view"
    INCIDENTS_MANAGE = "incidents.manage"
    BACKFILL_VIEW = "backfill.view"
    BACKFILL_RUN = "backfill.run"
    PERF_VIEW = "perf.view"
    ENV_VIEW = "env.view"
    ADMIN_VIEW = "admin.view"
    ADMIN_MANAGE = "admin.manage"
    IAM_VIEW = "iam.view"
    IAM_MANAGE = "iam.manage"
    DATA_PREVIEW = "data.preview"


_VIEWER = {Perm.DASHBOARD_VIEW, Perm.PIPELINES_VIEW, Perm.SCHEDULES_VIEW, Perm.PERF_VIEW, Perm.IAM_VIEW}
_ANALYST = {
    Perm.DASHBOARD_VIEW,
    Perm.CATALOG_VIEW,
    Perm.LINEAGE_VIEW,
    Perm.QUALITY_VIEW,
    Perm.DATA_PREVIEW,
    Perm.IAM_VIEW,
}
_DATA_ENGINEER = (
    _VIEWER
    | _ANALYST
    | {
        Perm.PIPELINES_RUN,
        Perm.PIPELINES_EDIT,
        Perm.SCHEDULES_EDIT,
        Perm.CATALOG_EDIT,
        Perm.LOGS_VIEW,
        Perm.INCIDENTS_MANAGE,
        Perm.BACKFILL_VIEW,
        Perm.BACKFILL_RUN,
        Perm.ENV_VIEW,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Perm]] = {
    Role.VIEWER: frozenset(_VIEWER),
    Role.ANALYST: frozenset(_ANALYST),
    Role.DATA_ENGINEER: frozenset(_DATA_ENGINEER),
    Role.ADMIN: frozenset(Perm),
}

ROLE_DESCRIPTIONS = {
    Role.ADMIN: "Administration complète",
    Role.DATA_ENGINEER: "Création, modification et exécution des pipelines",
    Role.ANALYST: "Consultation du catalogue, lineage et qualité",
    Role.VIEWER: "Consultation des dashboards et historiques",
}


class PermissionDenied(PermissionError):
    def __init__(self, perm: Perm):
        super().__init__(f"Permission refusée : {perm.value}")
        self.perm = perm


def normalize_roles(raw_roles) -> frozenset[Role]:
    """Keep only Data Hub roles (Keycloak also returns default-roles-*, offline_access...)."""
    known = {r.value for r in Role}
    roles = set()
    for raw in raw_roles or []:
        if isinstance(raw, str) and raw.upper() in known:
            roles.add(Role(raw.upper()))
    return frozenset(roles)


def permissions_for(roles) -> frozenset[Perm]:
    perms: set[Perm] = set()
    for role in roles:
        perms |= ROLE_PERMISSIONS.get(Role(role), frozenset())
    return frozenset(perms)


def highest_role(roles) -> Role | None:
    for role in (Role.ADMIN, Role.DATA_ENGINEER, Role.ANALYST, Role.VIEWER):
        if role in roles:
            return role
    return None
