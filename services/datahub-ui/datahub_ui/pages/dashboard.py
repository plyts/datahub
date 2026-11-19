"""Home dashboard: health of pipelines, data quality and platform at a glance."""

import time

import pandas as pd
import streamlit as st

from datahub_ui.health import check_all
from datahub_ui.ui import badge, fmt_duration, fmt_ts, header, safe


def render(ctx) -> None:
    header(None, "Data Hub", f"Plateforme Data — environnement **{ctx.settings.env.upper()}**")
    since = time.time() - 24 * 3600
    runs = safe(lambda: ctx.dagster.runs(limit=500, created_after=since), [], "Historique des runs")
    assets = safe(ctx.dagster.assets, [], "Catalogue des assets")
    schedules = safe(ctx.dagster.schedules, [], "Planification")

    statuses = pd.Series([r["status"] for r in runs], dtype="object")
    ok, ko = int((statuses == "SUCCESS").sum()), int((statuses == "FAILURE").sum())
    running = int(statuses.isin(["STARTED", "STARTING", "QUEUED"]).sum())
    stale = [a for a in assets if a.get("freshness") in ("DEGRADED", "WARNING")]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Runs 24 h", len(runs))
    c2.metric("Succès", ok)
    c3.metric("Échecs", ko, delta=f"-{ko}" if ko else None, delta_color="inverse")
    c4.metric("En cours / file", running)
    c5.metric("Assets non frais", len(stale), help="Politique de fraîcheur en WARNING / DEGRADED")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Derniers runs")
        if runs:
            df = pd.DataFrame(
                [
                    {
                        "Statut": badge(r["status"]),
                        "Pipeline": r["jobName"],
                        "Partition": r["tags"].get("dagster/partition", ""),
                        "Début": fmt_ts(r["startTime"]),
                        "Durée": fmt_duration(r["duration"]),
                        "Run": r["runId"][:8],
                    }
                    for r in runs[:15]
                ]
            )
            st.dataframe(df, hide_index=True, use_container_width=True)
        else:
            st.info("Aucun run sur les dernières 24 heures.")
    with right:
        st.subheader("Prochaines exécutions")
        ticks = sorted(
            ((t, s["name"]) for s in schedules if s["status"] == "RUNNING" for t in s["next_ticks"][:1]),
            key=lambda x: x[0],
        )
        for ts, name in ticks[:6]:
            st.write(f"🕒 **{fmt_ts(ts)}** — `{name}`")
        if not ticks:
            st.caption("Aucun schedule actif.")

        st.subheader("Santé de la plateforme")
        for h in check_all(ctx):
            st.write(
                f"{'🟢' if h.healthy else '🔴'} {h.name}" + (f" — {h.detail}" if h.detail and not h.healthy else "")
            )

    if stale:
        st.subheader("Fraîcheur à surveiller")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Asset": a["key"],
                        "Fraîcheur": badge(a["freshness"]),
                        "Dernière matérialisation": fmt_ts(a["last_materialization"]),
                    }
                    for a in stale
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    st.caption(
        f"Supervision détaillée : [Grafana]({ctx.settings.grafana_public_url}) · "
        f"[Dagster]({ctx.settings.dagster_public_url})"
    )
