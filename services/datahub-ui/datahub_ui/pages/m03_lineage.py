"""03. Data Lineage Explorer — upstream/downstream, impact analysis, column-level lineage, history."""

import pandas as pd
import streamlit as st

from datahub_ui.lineage import (
    build_graph,
    catalog_schema,
    column_lineage,
    downstream,
    impact_analysis,
    to_dot,
    upstream,
)
from datahub_ui.ui import fmt_ts, header, safe


def render(ctx) -> None:
    header("03", "Data Lineage Explorer", "Lineage des assets et modèles dbt, analyse d'impact et lineage de colonnes")
    assets = safe(ctx.dagster.assets, [], "Assets")
    if not assets:
        st.info("Aucun asset disponible.")
        return
    graph = build_graph(assets)
    keys = sorted(graph)
    default = keys.index("marts/fct_orders") if "marts/fct_orders" in keys else 0
    c1, c2, c3 = st.columns([3, 1, 1])
    focus = c1.selectbox("Asset", keys, index=default, key="p03_focus")
    depth = c2.slider("Profondeur", 1, 6, 3, key="p03_depth")
    direction = c3.radio("Sens", ["Les deux", "Amont", "Aval"], key="p03_dir")
    up = upstream(graph, focus, depth) if direction in ("Les deux", "Amont") else {}
    down = downstream(graph, focus, depth) if direction in ("Les deux", "Aval") else {}
    st.graphviz_chart(to_dot(graph, focus=focus, nodes={focus, *up, *down}), use_container_width=True)

    tabs = st.tabs(["💥 Analyse d'impact", "🧬 Lineage de colonnes", "🕓 Historique des transformations"])
    artifacts = ctx.artifacts()
    with tabs[0]:
        impact = impact_analysis(graph, focus, artifacts.exposures())
        c1, c2, c3 = st.columns(3)
        c1.metric("Assets impactés", impact["count"])
        c2.metric("Profondeur max", impact["max_depth"])
        c3.metric("Exposures impactées", len(impact["exposures"]))
        for layer, items in impact["by_layer"].items():
            st.write(f"**{layer}** : " + ", ".join(f"`{i}`" for i in items))
        for exposure in impact["exposures"]:
            st.warning(f"📊 Exposure impactée : {exposure}")
        if not impact["count"]:
            st.success("Aucun asset en aval : modification sans impact.")
    with tabs[1]:
        _column_lineage(artifacts, focus)
    with tabs[2]:
        history = safe(lambda: ctx.dagster.materializations(focus, limit=50), [], "Historique")
        if history:
            df = pd.DataFrame(
                [
                    {
                        "Date": fmt_ts(m["timestamp"]),
                        "Partition": m["partition"] or "",
                        "Lignes": m["metadata"].get("dagster/row_count"),
                        "Run": m["run_id"][:8],
                    }
                    for m in history
                ]
            )
            st.dataframe(df, hide_index=True, use_container_width=True)
        else:
            st.info("Jamais matérialisé.")


def _column_lineage(artifacts, focus: str) -> None:
    if not artifacts.available:
        st.info("Artefacts dbt non disponibles : lancez le job `dbt_docs_job`.")
        return
    models = {n["asset_key"]: n for n in artifacts.documented_nodes() if n["resource_type"] == "model"}
    if focus not in models:
        st.info("Le lineage de colonnes est disponible pour les modèles dbt (SQL compilé analysé avec sqlglot).")
        return
    node = models[focus]
    column = st.selectbox("Colonne", [c["name"] for c in node["columns"]], key="p03_col")
    _, compiled = artifacts.sql(node["unique_id"])
    sources = column_lineage(compiled, column, catalog_schema(artifacts.catalog))
    if sources:
        st.dataframe(
            pd.DataFrame(sources).rename(columns={"table": "Table source", "column": "Colonne source"}), hide_index=True
        )
    else:
        st.info("Lineage non déterminable pour cette colonne (expression complexe ou SQL non compilé).")
