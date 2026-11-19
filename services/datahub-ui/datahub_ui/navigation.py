"""Page registry: each page declares the permission needed to see it (RBAC-filtered navigation)."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

from datahub_ui.rbac import Perm


@dataclass(frozen=True)
class PageSpec:
    module: str
    title: str
    icon: str
    url_path: str
    permission: Perm
    section: str


PAGES: list[PageSpec] = [
    PageSpec("dashboard", "Dashboard", ":material/dashboard:", "dashboard", Perm.DASHBOARD_VIEW, "Accueil"),
    PageSpec(
        "m01_pipelines",
        "01 · Pipeline Management",
        ":material/account_tree:",
        "pipelines",
        Perm.PIPELINES_VIEW,
        "Orchestration",
    ),
    PageSpec(
        "m02_scheduling",
        "02 · Scheduling Center",
        ":material/schedule:",
        "scheduling",
        Perm.SCHEDULES_VIEW,
        "Orchestration",
    ),
    PageSpec(
        "m07_backfill",
        "07 · Backfill & Recovery",
        ":material/history:",
        "backfill",
        Perm.BACKFILL_VIEW,
        "Orchestration",
    ),
    PageSpec(
        "m03_lineage", "03 · Data Lineage Explorer", ":material/hub:", "lineage", Perm.LINEAGE_VIEW, "Gouvernance"
    ),
    PageSpec("m04_catalog", "04 · Data Catalog", ":material/menu_book:", "catalog", Perm.CATALOG_VIEW, "Gouvernance"),
    PageSpec(
        "m05_quality", "05 · Data Quality Center", ":material/verified:", "quality", Perm.QUALITY_VIEW, "Gouvernance"
    ),
    PageSpec(
        "m06_logs", "06 · Logs & Error Management", ":material/bug_report:", "logs", Perm.LOGS_VIEW, "Observabilité"
    ),
    PageSpec(
        "m08_performance",
        "08 · Performance Monitoring",
        ":material/speed:",
        "performance",
        Perm.PERF_VIEW,
        "Observabilité",
    ),
    PageSpec(
        "m09_environments",
        "09 · Environment Management",
        ":material/layers:",
        "environments",
        Perm.ENV_VIEW,
        "Plateforme",
    ),
    PageSpec(
        "m10_admin",
        "10 · Administration & Security",
        ":material/admin_panel_settings:",
        "admin",
        Perm.ADMIN_VIEW,
        "Plateforme",
    ),
    PageSpec("m11_iam", "11 · Identity & Access Management", ":material/badge:", "iam", Perm.IAM_VIEW, "Plateforme"),
]


def visible_pages(user) -> list[PageSpec]:
    return [p for p in PAGES if user.can(p.permission)]


def render_function(spec: PageSpec):
    return import_module(f"datahub_ui.pages.{spec.module}").render
