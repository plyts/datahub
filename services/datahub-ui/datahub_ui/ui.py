"""Shared UI helpers (formatting, status badges, safe backend calls)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import streamlit as st

log = logging.getLogger("datahub.ui")

STATUS_ICONS = {
    "SUCCESS": "🟢",
    "SUCCEEDED": "🟢",
    "MATERIALIZED": "🟢",
    "HEALTHY": "🟢",
    "RUNNING": "🟢",
    "COMPLETED": "🟢",
    "COMPLETED_SUCCESS": "🟢",
    "resolved": "🟢",
    "FAILURE": "🔴",
    "FAILED": "🔴",
    "DEGRADED": "🔴",
    "COMPLETED_FAILED": "🔴",
    "EXECUTION_FAILED": "🔴",
    "open": "🔴",
    "STARTED": "🔵",
    "STARTING": "🔵",
    "QUEUED": "🟡",
    "MATERIALIZING": "🔵",
    "IN_PROGRESS": "🔵",
    "REQUESTED": "🔵",
    "acknowledged": "🟡",
    "WARNING": "🟠",
    "WARN": "🟠",
    "CANCELED": "⚪",
    "CANCELING": "⚪",
    "STOPPED": "⚪",
    "UNKNOWN": "⚪",
    "NOT_APPLICABLE": "⚪",
    "SKIPPED": "⚪",
}


def badge(status: str | None) -> str:
    if not status:
        return "⚪ —"
    return f"{STATUS_ICONS.get(status, '⚪')} {status}"


def fmt_ts(ts: float | None, tz: str = "Europe/Paris") -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(float(ts), tz=UTC).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M:%S")


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, sec = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {sec:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def header(number: str | None, title: str, subtitle: str = "") -> None:
    prefix = f"{number}. " if number else ""
    st.title(f"{prefix}{title}")
    if subtitle:
        st.caption(subtitle)


def safe[T](fn: Callable[[], T], default: T, what: str) -> T:
    """Call a backend; on failure show a warning and return `default` (the page stays usable)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - every backend error is shown to the user
        log.warning("%s failed: %s", what, exc)
        st.warning(f"{what} indisponible : {exc}")
        return default


def denied(perm_label: str) -> None:
    st.info(f"🔒 Action réservée : permission « {perm_label} » requise.")
