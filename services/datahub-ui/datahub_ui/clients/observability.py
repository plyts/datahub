"""Clients for Loki (logs) and Prometheus (metrics)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests


class ObservabilityError(RuntimeError):
    pass


def _get(url: str, params: dict, timeout: float) -> dict:
    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ObservabilityError(f"{url} : {exc}") from exc
    if payload.get("status") not in (None, "success"):
        raise ObservabilityError(payload.get("error") or str(payload))
    return payload


@dataclass
class LokiClient:
    url: str
    timeout: float = 15.0

    def ready(self) -> bool:
        try:
            return requests.get(f"{self.url}/ready", timeout=5).status_code == 200
        except requests.RequestException:
            return False

    def labels(self, label: str = "container") -> list[str]:
        return _get(f"{self.url}/loki/api/v1/label/{label}/values", {}, self.timeout).get("data", [])

    def query_range(self, logql: str, minutes: int = 60, limit: int = 500, direction: str = "backward") -> list[dict]:
        end = time.time_ns()
        start = end - minutes * 60 * 1_000_000_000
        payload = _get(
            f"{self.url}/loki/api/v1/query_range",
            {"query": logql, "start": start, "end": end, "limit": limit, "direction": direction},
            self.timeout,
        )
        lines = []
        for stream in payload.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                lines.append({"ts": int(ts) / 1e9, "line": line, **{f"label_{k}": v for k, v in labels.items()}})
        lines.sort(key=lambda r: r["ts"], reverse=direction == "backward")
        return lines[:limit]


def build_logql(
    container: str | None = None, contains: str | None = None, level: str | None = None, regex: str | None = None
) -> str:
    """Build a LogQL query from UI filters (values are escaped)."""

    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    selector = f'{{container="{esc(container)}"}}' if container else '{job="docker"}'
    query = selector
    if level:
        query += f' |~ "(?i){esc(level)}"'
    if contains:
        query += f' |= "{esc(contains)}"'
    if regex:
        query += f' |~ "{esc(regex)}"'
    return query


@dataclass
class PrometheusClient:
    url: str
    timeout: float = 15.0

    def ready(self) -> bool:
        try:
            return requests.get(f"{self.url}/-/ready", timeout=5).status_code == 200
        except requests.RequestException:
            return False

    def query(self, promql: str) -> list[dict[str, Any]]:
        payload = _get(f"{self.url}/api/v1/query", {"query": promql}, self.timeout)
        return [
            {"labels": r.get("metric", {}), "value": float(r["value"][1])}
            for r in payload.get("data", {}).get("result", [])
        ]

    def scalar(self, promql: str, default: float | None = None) -> float | None:
        result = self.query(promql)
        return result[0]["value"] if result else default

    def query_range(self, promql: str, minutes: int = 60, step: str = "60s") -> list[dict]:
        end = time.time()
        payload = _get(
            f"{self.url}/api/v1/query_range",
            {"query": promql, "start": end - minutes * 60, "end": end, "step": step},
            self.timeout,
        )
        rows = []
        for series in payload.get("data", {}).get("result", []):
            name = ",".join(f"{k}={v}" for k, v in series.get("metric", {}).items() if k != "__name__") or promql
            for ts, value in series.get("values", []):
                rows.append({"ts": float(ts), "series": name, "value": float(value)})
        return rows

    def alerts(self) -> list[dict]:
        return _get(f"{self.url}/api/v1/alerts", {}, self.timeout).get("data", {}).get("alerts", [])

    def targets(self) -> list[dict]:
        active = _get(f"{self.url}/api/v1/targets", {}, self.timeout).get("data", {}).get("activeTargets", [])
        return [
            {
                "job": t["labels"].get("job"),
                "instance": t["labels"].get("instance"),
                "health": t.get("health"),
                "last_error": t.get("lastError"),
            }
            for t in active
        ]
