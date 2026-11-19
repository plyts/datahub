"""08. Performance Monitoring — durations, volumes, CPU / RAM / disk, run comparison, slowdowns."""

import time

import pandas as pd
import plotly.express as px
import streamlit as st

from datahub_ui.perf import compare_steps, detect_slowdowns, duration_stats, runs_frame
from datahub_ui.ui import fmt_ts, header, safe

SYSTEM_QUERIES = {
    "CPU %": '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))',
    "RAM %": "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
    "Disque % (/)": '100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})',
}


def render(ctx) -> None:
    header(
        "08", "Performance Monitoring", "Durées, volumes traités, ressources système, comparaison et ralentissements"
    )
    days = st.slider("Période (jours)", 1, 90, 14, key="p08_days")
    runs = safe(lambda: ctx.dagster.runs(limit=1000, created_after=time.time() - days * 86400), [], "Runs")
    df = runs_frame(runs)
    tabs = st.tabs(["⏱️ Durées", "🐢 Ralentissements", "⚖️ Comparaison", "📦 Volumes", "🖥️ Système"])
    with tabs[0]:
        stats = duration_stats(df)
        st.dataframe(stats, hide_index=True, use_container_width=True)
        done = df[df["duration"].notna()] if not df.empty else df
        if not done.empty:
            fig = px.scatter(
                done,
                x="started",
                y="duration",
                color="jobName",
                symbol="status",
                labels={"duration": "durée (s)", "started": "début"},
                height=380,
            )
            st.plotly_chart(fig, use_container_width=True)
    with tabs[1]:
        factor = st.slider("Seuil (× médiane historique)", 1.2, 3.0, 1.5, 0.1, key="p08_factor")
        slow = detect_slowdowns(df, factor=factor)
        if slow.empty:
            st.success("Aucun ralentissement détecté.")
        else:
            st.dataframe(slow, hide_index=True, use_container_width=True)
    with tabs[2]:
        _compare(ctx, runs)
    with tabs[3]:
        _volumes(ctx)
    with tabs[4]:
        _system(ctx)


def _compare(ctx, runs) -> None:
    finished = [r for r in runs if r["status"] in ("SUCCESS", "FAILURE")]
    if len(finished) < 2:
        st.info("Au moins deux runs terminés sont nécessaires.")
        return
    labels = {f"{r['jobName']} · {fmt_ts(r['startTime'])} · {r['runId'][:8]}": r["runId"] for r in finished}
    names = list(labels)
    c1, c2 = st.columns(2)
    a = c1.selectbox("Run A", names, index=1, key="p08_a")
    b = c2.selectbox("Run B", names, index=0, key="p08_b")
    run_a = safe(lambda: ctx.dagster.run(labels[a]), None, "Run A")
    run_b = safe(lambda: ctx.dagster.run(labels[b]), None, "Run B")
    if run_a and run_b:
        cmp = compare_steps(run_a["stepStats"], run_b["stepStats"])
        st.dataframe(cmp, hide_index=True, use_container_width=True)
        st.bar_chart(cmp.set_index("step")[["run_a_s", "run_b_s"]], height=300)


def _volumes(ctx) -> None:
    assets = safe(ctx.dagster.assets, [], "Assets")
    keys = [a["key"] for a in assets if a["key"].startswith(("raw/", "marts/"))]
    chosen = st.multiselect(
        "Assets", keys, default=[k for k in keys if k in ("raw/orders", "marts/fct_orders")], key="p08_vol_assets"
    )
    rows = []
    for key in chosen:
        for m in safe(lambda k=key: ctx.dagster.materializations(k, limit=100), [], f"Matérialisations {key}"):
            if m["metadata"].get("dagster/row_count") is not None:
                rows.append(
                    {
                        "asset": key,
                        "date": pd.to_datetime(m["timestamp"], unit="s"),
                        "lignes": m["metadata"]["dagster/row_count"],
                    }
                )
    if rows:
        st.plotly_chart(
            px.line(pd.DataFrame(rows), x="date", y="lignes", color="asset", markers=True, height=360),
            use_container_width=True,
        )
    else:
        st.info("Pas de métrique de volume (dagster/row_count).")


def _system(ctx) -> None:
    cols = st.columns(len(SYSTEM_QUERIES))
    for col, (label, promql) in zip(cols, SYSTEM_QUERIES.items(), strict=True):
        value = safe(lambda q=promql: ctx.prometheus.scalar(q), None, label)
        col.metric(label, f"{value:.1f}" if value is not None else "—")
    series = safe(lambda: ctx.prometheus.query_range(SYSTEM_QUERIES["CPU %"], minutes=180, step="120s"), [], "CPU")
    if series:
        st.plotly_chart(
            px.line(
                pd.DataFrame(series).assign(date=lambda d: pd.to_datetime(d["ts"], unit="s")),
                x="date",
                y="value",
                labels={"value": "CPU %"},
                height=260,
            ),
            use_container_width=True,
        )
    containers = safe(
        lambda: ctx.prometheus.query(
            'topk(10, sum by (name) (rate(container_cpu_usage_seconds_total{name=~".+"}[5m])) * 100)'
        ),
        [],
        "Conteneurs",
    )
    if containers:
        st.subheader("Top conteneurs (CPU %)")
        st.dataframe(
            pd.DataFrame([{"Conteneur": c["labels"].get("name"), "CPU %": round(c["value"], 2)} for c in containers]),
            hide_index=True,
        )
    st.caption(f"Dashboards détaillés : [Grafana]({ctx.settings.grafana_public_url}/dashboards)")
