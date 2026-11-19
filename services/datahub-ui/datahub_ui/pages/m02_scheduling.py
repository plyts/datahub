"""02. Scheduling Center — cron schedules, sensors, partitions and upcoming runs."""

from datetime import UTC, datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from datahub_ui.rbac import Perm
from datahub_ui.ui import badge, denied, fmt_ts, header, safe

CRON_PRESETS = {
    "Toutes les heures": "0 * * * *",
    "Chaque jour à 06:00": "0 6 * * *",
    "Jours ouvrés à 07:30": "30 7 * * 1-5",
    "Chaque lundi à 05:00": "0 5 * * 1",
    "Le 1er du mois à 04:00": "0 4 1 * *",
    "Toutes les 15 minutes": "*/15 * * * *",
}


def render(ctx) -> None:
    header(
        "02",
        "Scheduling Center",
        "Planifications cron, déclencheurs événementiels, partitions et prochaines exécutions",
    )
    schedules = safe(ctx.dagster.schedules, [], "Schedules")
    sensors = safe(ctx.dagster.sensors, [], "Sensors")
    tabs = st.tabs(["🗓️ Planifications", "📡 Sensors", "⏭️ Prochaines exécutions", "🧱 Partitions", "📘 Aide cron"])
    with tabs[0]:
        _schedules(ctx, schedules)
    with tabs[1]:
        _sensors(ctx, sensors)
    with tabs[2]:
        _upcoming(schedules)
    with tabs[3]:
        _partitions(ctx)
    with tabs[4]:
        st.dataframe(pd.DataFrame([{"Fréquence": k, "Cron": v} for k, v in CRON_PRESETS.items()]), hide_index=True)
        st.markdown(
            "Format : `minute heure jour-du-mois mois jour-de-semaine`. Fuseau : "
            f"**{ctx.settings.timezone}**. Pour planifier une nouvelle sélection d'assets, créez un pipeline "
            "personnalisé avec un cron (module 01)."
        )


def _schedules(ctx, schedules) -> None:
    if not schedules:
        st.info("Aucune planification.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Schedule": s["name"],
                    "Statut": badge(s["status"]),
                    "Cron": s["cron"],
                    "Fuseau": s["timezone"],
                    "Pipeline": s["job"],
                    "Prochaine exécution": fmt_ts(s["next_ticks"][0] if s["next_ticks"] else None),
                    "Description": s["description"] or "",
                }
                for s in schedules
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    if not ctx.allowed(Perm.SCHEDULES_EDIT):
        denied("schedules.edit")
        return
    c1, c2 = st.columns([3, 1])
    name = c1.selectbox("Schedule", [s["name"] for s in schedules], key="p02_sched")
    current = next(s for s in schedules if s["name"] == name)
    running = current["status"] == "RUNNING"
    if c2.button("⏸️ Arrêter" if running else "▶️ Démarrer", key="p02_sched_toggle"):
        ctx.user.require(Perm.SCHEDULES_EDIT)
        status = safe(lambda: ctx.dagster.set_schedule(name, not running), None, "Mise à jour du schedule")
        if status:
            ctx.audit("schedule.stop" if running else "schedule.start", name)
            st.rerun()


def _sensors(ctx, sensors) -> None:
    if not sensors:
        st.info("Aucun sensor.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Sensor": s["name"],
                    "Statut": badge(s["status"]),
                    "Type": s["type"],
                    "Intervalle (s)": s["interval_s"],
                    "Cible": ", ".join(s["targets"]),
                    "Dernier tick": f"{badge(s['last_tick_status'])} {fmt_ts(s['last_tick'])}",
                    "Description": s["description"] or "",
                }
                for s in sensors
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    if not ctx.allowed(Perm.SCHEDULES_EDIT):
        return
    c1, c2 = st.columns([3, 1])
    name = c1.selectbox("Sensor", [s["name"] for s in sensors], key="p02_sensor")
    running = next(s for s in sensors if s["name"] == name)["status"] == "RUNNING"
    if c2.button("⏸️ Arrêter" if running else "▶️ Démarrer", key="p02_sensor_toggle"):
        ctx.user.require(Perm.SCHEDULES_EDIT)
        if safe(lambda: ctx.dagster.set_sensor(name, not running), None, "Mise à jour du sensor"):
            ctx.audit("sensor.stop" if running else "sensor.start", name)
            st.rerun()


def _upcoming(schedules) -> None:
    rows = [
        {"Schedule": s["name"], "Exécution": datetime.fromtimestamp(t, tz=UTC)}
        for s in schedules
        if s["status"] == "RUNNING"
        for t in s["next_ticks"]
    ]
    if not rows:
        st.info("Aucune exécution planifiée (aucun schedule actif).")
        return
    df = pd.DataFrame(rows).sort_values("Exécution")
    fig = px.scatter(df, x="Exécution", y="Schedule", color="Schedule", height=320)
    fig.update_traces(marker={"size": 12, "symbol": "diamond"})
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        df.assign(Exécution=df["Exécution"].dt.tz_convert("Europe/Paris").dt.strftime("%Y-%m-%d %H:%M")),
        hide_index=True,
        use_container_width=True,
    )


def _partitions(ctx) -> None:
    assets = [a for a in safe(ctx.dagster.assets, [], "Assets") if a["partitioned"]]
    if not assets:
        st.info("Aucun asset partitionné.")
        return
    rows = []
    for a in assets:
        stats = a.get("partition_stats") or {}
        total = stats.get("numPartitions") or 0
        done = stats.get("numMaterialized") or 0
        rows.append(
            {
                "Asset": a["key"],
                "Partitions": total,
                "Matérialisées": done,
                "En échec": stats.get("numFailed") or 0,
                "Manquantes": max(total - done - (stats.get("numFailed") or 0), 0),
                "Couverture %": round(100 * done / total, 1) if total else 0,
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    key = st.selectbox("Détail des plages", [a["key"] for a in assets], key="p02_part_asset")
    ranges = safe(lambda: ctx.dagster.partition_ranges(key), [], "Statut des partitions")
    if ranges:
        st.dataframe(
            pd.DataFrame([{"Du": r["startKey"], "Au": r["endKey"], "Statut": badge(r["status"])} for r in ranges]),
            hide_index=True,
        )
    st.caption(
        "Dépendances : les modèles dbt en aval sont recalculés dans le même run que la partition "
        "(job `daily_partition_job`) ; la documentation est régénérée par un sensor après chaque succès."
    )
