"""Health of every Data Hub service (Administration module, dashboard and exporter)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class ServiceHealth:
    name: str
    healthy: bool
    latency_ms: float | None
    detail: str = ""


def _timed(name: str, fn: Callable[[], bool | tuple[bool, str]]) -> ServiceHealth:
    start = time.perf_counter()
    try:
        result = fn()
        ok, detail = result if isinstance(result, tuple) else (bool(result), "")
    except Exception as exc:  # noqa: BLE001 - a health probe must never raise
        ok, detail = False, str(exc)[:200]
    return ServiceHealth(name, ok, round((time.perf_counter() - start) * 1000, 1), detail)


def http_ok(url: str, timeout: float = 5) -> bool:
    try:
        return requests.get(url, timeout=timeout).status_code < 400
    except requests.RequestException:
        return False


def check_all(ctx) -> list[ServiceHealth]:
    s = ctx.settings
    checks = [
        ("Dagster webserver", lambda: (ctx.dagster.ping(), "GraphQL")),
        ("Dagster daemon", lambda: _daemons_ok(ctx)),
        ("PostgreSQL (app)", lambda: (ctx.db is not None and ctx.db.ping(), "datahub")),
        ("Keycloak", lambda: (ctx.keycloak.ready(), f"realm {s.keycloak_realm}")),
        ("Prometheus", lambda: ctx.prometheus.ready()),
        ("Loki", lambda: ctx.loki.ready()),
        ("Grafana", lambda: http_ok(f"{s.grafana_url}/api/health")),
    ]
    return [_timed(name, fn) for name, fn in checks]


def _daemons_ok(ctx) -> tuple[bool, str]:
    statuses = ctx.dagster.daemon_health()
    unhealthy = [d["daemonType"] for d in statuses if d["required"] and not d["healthy"]]
    return (not unhealthy, "KO : " + ", ".join(unhealthy) if unhealthy else f"{len(statuses)} daemons")
