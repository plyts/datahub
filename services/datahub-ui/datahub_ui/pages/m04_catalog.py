"""04. Data Catalog — databases, schemas, tables, columns, SQL and business glossary."""

import pandas as pd
import streamlit as st

from datahub_ui.clients.duckdb_reader import DatabaseBusy, preview
from datahub_ui.db.repository import ValidationError
from datahub_ui.rbac import Perm, Role
from datahub_ui.ui import denied, header, safe


def render(ctx) -> None:
    header("04", "Data Catalog", "Catalogue des schémas et tables, documentation, SQL et glossaire métier")
    artifacts = ctx.artifacts()
    tabs = st.tabs(["📚 Catalogue", "📖 Glossaire métier", "🌐 Documentation dbt"])
    with tabs[0]:
        if not artifacts.available:
            st.info(
                "Artefacts dbt absents. Ils sont publiés par le job `dbt_docs_job` (après chaque pipeline quotidien)."
            )
        else:
            _catalog(ctx, artifacts)
    with tabs[1]:
        _glossary(ctx)
    with tabs[2]:
        st.markdown(
            f"Site de documentation dbt complet : [{ctx.settings.public_url}/dbt-docs/]({ctx.settings.public_url}/dbt-docs/)"
        )
        st.caption(f"Artefacts générés le : {artifacts.generated_at or '—'}")


def _catalog(ctx, artifacts) -> None:
    nodes = artifacts.documented_nodes()
    c1, c2 = st.columns([1, 2])
    schemas = sorted({n["schema"] for n in nodes if n["schema"]})
    schema = c1.selectbox("Schéma", ["(tous)", *schemas], key="p04_schema")
    query = c2.text_input("Recherche (nom, description, colonne, tag)", key="p04_search").lower().strip()

    def match(n):
        if schema != "(tous)" and n["schema"] != schema:
            return False
        if not query:
            return True
        haystack = " ".join(
            [n["name"], n["description"], " ".join(n["tags"]), " ".join(c["name"] for c in n["columns"])]
        ).lower()
        return query in haystack

    selected = [n for n in nodes if match(n)]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Table": n["relation"],
                    "Type": n["resource_type"],
                    "Matérialisation": n["materialized"],
                    "Lignes": n["row_count"],
                    "Colonnes": len(n["columns"]),
                    "Owner": n["owner"],
                    "Description": n["description"][:100],
                }
                for n in selected
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    if not selected:
        return
    relation = st.selectbox("Fiche détaillée", [n["relation"] for n in selected], key="p04_detail")
    node = next(n for n in selected if n["relation"] == relation)
    st.subheader(f"`{node['relation']}`")
    st.write(node["description"] or "_Pas de description._")
    meta = st.columns(4)
    meta[0].write(f"**Type** : {node['resource_type']}")
    meta[1].write(f"**Owner** : {node['owner'] or '—'}")
    meta[2].write(f"**Tags** : {', '.join(node['tags']) or '—'}")
    meta[3].write(f"**Glossaire** : {', '.join(node['glossary']) or '—'}")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Colonne": c["name"],
                    "Type": c["type"],
                    "Description": c["description"],
                    "PII": "🔒" if c["pii"] else "",
                    "Tests": ", ".join(c["tests"]),
                }
                for c in node["columns"]
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    if node["resource_type"] in ("model", "snapshot"):
        raw, compiled = artifacts.sql(node["unique_id"])
        t1, t2 = st.tabs(["SQL source", "SQL compilé"])
        t1.code(raw or "—", language="sql")
        t2.code(compiled or "Non compilé (exécuter dbt docs generate).", language="sql")
    if ctx.allowed(Perm.DATA_PREVIEW) and st.toggle("Aperçu des données (100 lignes)", key="p04_preview"):
        schema_name, table = node["relation"].split(".", 1)
        try:
            df = preview(ctx.settings.duckdb_path, schema_name, table)
            pii = [c["name"] for c in node["columns"] if c["pii"]]
            if pii and not ({Role.ADMIN, Role.DATA_ENGINEER} & ctx.user.roles):
                df[[c for c in pii if c in df.columns]] = "••••••"
                st.caption("Colonnes PII masquées pour votre rôle.")
            ctx.audit("data.preview", node["relation"])
            st.dataframe(df, use_container_width=True)
        except (DatabaseBusy, FileNotFoundError, ValueError) as exc:
            st.warning(str(exc))


def _glossary(ctx) -> None:
    if ctx.db is None:
        st.warning("Base applicative non configurée.")
        return
    terms = safe(ctx.db.list_terms, [], "Glossaire")
    search = st.text_input("Rechercher un terme", key="p04_gsearch").lower()
    shown = [t for t in terms if search in (t["term"] + t["definition"] + " ".join(t["synonyms"])).lower()]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Terme": t["term"],
                    "Définition": t["definition"],
                    "Synonymes": ", ".join(t["synonyms"]),
                    "Assets liés": ", ".join(t["related_assets"]),
                    "Owner": t["owner"],
                }
                for t in shown
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    if not ctx.allowed(Perm.CATALOG_EDIT):
        denied("catalog.edit")
        return
    with st.form("p04_term_form"):
        st.subheader("Ajouter / modifier un terme")
        term = st.text_input("Terme")
        definition = st.text_area("Définition")
        owner = st.text_input("Owner")
        synonyms = st.text_input("Synonymes (séparés par des virgules)")
        related = st.text_input("Assets liés (ex. marts/fct_orders, séparés par des virgules)")
        if st.form_submit_button("💾 Enregistrer", type="primary"):
            ctx.user.require(Perm.CATALOG_EDIT)
            try:
                ctx.db.save_term(
                    {
                        "term": term,
                        "definition": definition,
                        "owner": owner,
                        "synonyms": synonyms.split(","),
                        "related_assets": related.split(","),
                    },
                    ctx.user.username,
                )
                ctx.audit("glossary.save", term)
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))
