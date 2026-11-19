"""05. Data Quality Center — dbt tests & asset checks, freshness, anomaly detection, history."""

import statistics

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from datahub_ui.clients.duckdb_reader import DatabaseBusy
from datahub_ui.ui import badge, fmt_ts, header, safe

CHECK_STATUS_OK = {"SUCCEEDED"}


def render(ctx) -> None:
    header("05", "Data Quality Center", "Tests dbt, NULL / unicité / intégrité référentielle, fraîcheur et anomalies")
    assets = safe(ctx.dagster.assets, [], "Assets")
    checks = []
    for asset in assets:
        if asset["has_checks"]:
            checks.extend(safe(lambda a=asset: ctx.dagster.asset_checks(a["key"]), [], f"Checks de {asset['key']}"))
    passed = sum(1 for c in checks if c["status"] in CHECK_STATUS_OK)
    failed = [c for c in checks if c["status"] in ("FAILED", "EXECUTION_FAILED")]
    never = sum(1 for c in checks if not c["status"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Contrôles", len(checks))
    c2.metric("Réussis", passed)
    c3.metric("En échec", len(failed))
    c4.metric("Jamais exécutés", never)

    tabs = st.tabs(["✅ Contrôles", "⏱️ Fraîcheur", "📈 Anomalies de volume", "🕓 Historique", "🧪 Derniers tests dbt"])
    with tabs[0]:
        kind = st.multiselect(
            "Type de contrôle",
            ["unique", "not_null", "relationships", "accepted_values", "not_negative", "anomaly", "autre"],
            key="p05_kind",
        )
        rows = []
        for c in checks:
            ctype = next(
                (
                    k
                    for k in ("unique", "not_null", "relationships", "accepted_values", "not_negative")
                    if c["name"].startswith(k)
                ),
                "anomaly" if "anomaly" in c["name"] else "autre",
            )
            if kind and ctype not in kind:
                continue
            rows.append(
                {
                    "Asset": c["asset"],
                    "Contrôle": c["name"],
                    "Type": ctype,
                    "Statut": badge(c["status"]),
                    "Sévérité": c["severity"] or "",
                    "Bloquant": c["blocking"],
                    "Exécuté": fmt_ts(c["timestamp"]),
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    with tabs[1]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Asset": a["key"],
                        "Fraîcheur": badge(a["freshness"]),
                        "Dernière matérialisation": fmt_ts(a["last_materialization"]),
                    }
                    for a in assets
                    if a.get("freshness")
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
        st.caption("Politiques : données brutes J-1 chargées avant 09:00 ; marts reconstruits il y a moins de 26 h.")
    with tabs[2]:
        _volume_anomalies(ctx)
    with tabs[3]:
        if checks:
            label = st.selectbox("Contrôle", [f"{c['asset']} · {c['name']}" for c in checks], key="p05_hist")
            asset_key, check_name = label.split(" · ", 1)
            history = safe(lambda: ctx.dagster.check_history(asset_key, check_name), [], "Historique du contrôle")
            if history:
                df = pd.DataFrame(history)
                df["date"] = pd.to_datetime(df["timestamp"], unit="s")
                df["résultat"] = df["status"].map(lambda s: 1 if s in CHECK_STATUS_OK else 0)
                st.bar_chart(df.set_index("date")["résultat"], height=200)
                st.dataframe(df[["date", "status", "severity", "partition", "run_id"]], hide_index=True)
    with tabs[4]:
        results = ctx.artifacts().test_results()
        if results:
            st.dataframe(pd.DataFrame(results), hide_index=True, use_container_width=True)
        else:
            st.info("Aucun run_results.json publié.")


def _daily_volumes(path: str) -> pd.DataFrame:
    import duckdb

    try:
        with duckdb.connect(path, read_only=True) as conn:
            return conn.execute(
                "select _partition_date as day, count(*) as orders from raw.orders group by 1 order by 1"
            ).fetchdf()
    except duckdb.IOException as exc:
        raise DatabaseBusy("Base DuckDB occupée par un pipeline.") from exc


def _volume_anomalies(ctx) -> None:
    try:
        df = _daily_volumes(ctx.settings.duckdb_path)
    except Exception as exc:  # noqa: BLE001
        st.info(f"Volumes indisponibles : {exc}")
        return
    if len(df) < 2:
        st.info("Historique insuffisant.")
        return
    mean, stdev = statistics.fmean(df["orders"]), statistics.pstdev(df["orders"])
    df["zscore"] = (df["orders"] - mean) / stdev if stdev else 0
    fig = go.Figure()
    fig.add_scatter(x=df["day"], y=df["orders"], mode="lines+markers", name="Commandes / jour")
    fig.add_hrect(
        y0=mean - 3 * stdev, y1=mean + 3 * stdev, fillcolor="green", opacity=0.08, line_width=0, annotation_text="± 3 σ"
    )
    anomalies = df[df["zscore"].abs() > 3]
    fig.add_scatter(
        x=anomalies["day"], y=anomalies["orders"], mode="markers", name="Anomalies", marker={"color": "red", "size": 12}
    )
    fig.update_layout(height=360, margin={"t": 20})
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"{len(anomalies)} anomalie(s) détectée(s) (|z| > 3). Le check `orders_volume_anomaly` "
        "évalue la dernière partition à chaque run."
    )
