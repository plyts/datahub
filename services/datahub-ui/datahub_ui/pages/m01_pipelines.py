"""01. Pipeline Management — discover, run, configure and follow pipelines."""

import json
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from datahub_ui.db.repository import ValidationError
from datahub_ui.lineage import build_graph, to_dot
from datahub_ui.rbac import Perm
from datahub_ui.ui import badge, denied, fmt_duration, fmt_ts, header, safe

PARTITIONED_JOBS = {"daily_partition_job"}
STATUSES = ["SUCCESS", "FAILURE", "STARTED", "QUEUED", "CANCELED"]


def render(ctx) -> None:
    header("01", "Pipeline Management", "Création, configuration, exécution et suivi des pipelines")
    tabs = st.tabs(
        ["▶️ Pipelines", "🧩 Assets & modèles", "🔗 Dépendances", "📜 Historique", "🛠️ Pipelines personnalisés"]
    )
    jobs = safe(ctx.dagster.jobs, [], "Liste des pipelines")
    assets = safe(ctx.dagster.assets, [], "Assets Dagster")
    with tabs[0]:
        _jobs_tab(ctx, jobs, assets)
    with tabs[1]:
        _assets_tab(ctx, assets)
    with tabs[2]:
        _deps_tab(assets)
    with tabs[3]:
        _history_tab(ctx, jobs)
    with tabs[4]:
        _custom_tab(ctx, jobs)


def _jobs_tab(ctx, jobs, assets) -> None:
    if not jobs:
        st.info("Aucun pipeline chargé.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Pipeline": j["name"],
                    "Description": j["description"] or "",
                    "Type": j["tags"].get("datahub/kind", ""),
                }
                for j in jobs
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    st.subheader("Exécution manuelle")
    if not ctx.allowed(Perm.PIPELINES_RUN):
        denied("pipelines.run")
        return
    names = [j["name"] for j in jobs]
    job = st.selectbox("Pipeline", names, key="p01_job")
    partition = None
    partitioned = job in PARTITIONED_JOBS or (
        job.startswith("custom_") and st.checkbox("Pipeline partitionné : choisir un jour", key="p01_is_part")
    )
    if partitioned:
        day = st.date_input("Partition (jour)", value=date.today() - timedelta(days=1), key="p01_partition")
        partition = day.isoformat()
    config_text = st.text_area("Configuration du run (JSON, optionnel)", value="{}", key="p01_config", height=80)
    if st.button("🚀 Lancer", type="primary", key="p01_launch"):
        try:
            run_config = json.loads(config_text or "{}")
        except json.JSONDecodeError as exc:
            st.error(f"JSON invalide : {exc}")
            return
        ctx.user.require(Perm.PIPELINES_RUN)
        try:
            run_id = ctx.dagster.launch_job(
                job, run_config=run_config, partition_key=partition, tags={"datahub/user": ctx.user.username}
            )
            ctx.audit("pipeline.launch", job, {"run_id": run_id, "partition": partition})
            st.success(
                f"Run lancé : `{run_id}` — [suivre dans Dagster]({ctx.settings.dagster_public_url}/runs/{run_id})"
            )
        except Exception as exc:  # noqa: BLE001
            ctx.audit("pipeline.launch", job, {"error": str(exc)}, success=False)
            st.error(str(exc))


def _assets_tab(ctx, assets) -> None:
    if not assets:
        st.info("Aucun asset.")
        return
    groups = sorted({a["group"] for a in assets})
    chosen = st.multiselect("Groupes", groups, default=groups, key="p01_groups")
    rows = [
        {
            "Asset": a["key"],
            "Groupe": a["group"],
            "Type": ", ".join(a["kinds"]),
            "Partitionné": a["partitioned"],
            "Checks": a["has_checks"],
            "Fraîcheur": badge(a["freshness"]),
            "Dernière matérialisation": fmt_ts(a["last_materialization"]),
            "Propriétaires": ", ".join(a["owners"]),
            "Description": a["description"][:120],
        }
        for a in assets
        if a["group"] in chosen
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption(f"{len(rows)} assets — modèles dbt découverts automatiquement depuis le manifest dbt (dagster-dbt).")


def _deps_tab(assets) -> None:
    if assets:
        st.graphviz_chart(to_dot(build_graph(assets)), use_container_width=True)


def _history_tab(ctx, jobs) -> None:
    c1, c2, c3 = st.columns(3)
    job = c1.selectbox("Pipeline", ["(tous)"] + [j["name"] for j in jobs], key="p01_hist_job")
    statuses = c2.multiselect("Statuts", STATUSES, key="p01_hist_status")
    limit = c3.number_input("Nombre de runs", 10, 500, 50, step=10, key="p01_hist_limit")
    runs = safe(
        lambda: ctx.dagster.runs(
            limit=int(limit), job_name=None if job == "(tous)" else job, statuses=statuses or None
        ),
        [],
        "Historique",
    )
    if not runs:
        st.info("Aucun run.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Statut": badge(r["status"]),
                    "Pipeline": r["jobName"],
                    "Partition": r["tags"].get("dagster/partition", ""),
                    "Créé": fmt_ts(r["creationTime"]),
                    "Durée": fmt_duration(r["duration"]),
                    "Lancé par": r["tags"].get("datahub/user", "schedule/sensor"),
                    "Run ID": r["runId"],
                }
                for r in runs
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    run_id = st.selectbox("Détail d'un run", [r["runId"] for r in runs], key="p01_run_detail")
    run = safe(lambda: ctx.dagster.run(run_id), None, "Détail du run")
    if not run:
        return
    st.write(
        f"{badge(run['status'])} — `{run['jobName']}` — [ouvrir dans Dagster]({ctx.settings.dagster_public_url}/runs/{run_id})"
    )
    steps = pd.DataFrame(
        [
            {
                "Étape": s["stepKey"],
                "Statut": badge(s["status"]),
                "Durée": fmt_duration((s["endTime"] or 0) - (s["startTime"] or 0) if s["startTime"] else None),
            }
            for s in run["stepStats"]
        ]
    )
    st.dataframe(steps, hide_index=True, use_container_width=True)
    if ctx.allowed(Perm.PIPELINES_RUN):
        b1, b2 = st.columns(2)
        if run["status"] == "FAILURE" and b1.button("🔁 Relancer depuis l'échec", key="p01_reexec"):
            ctx.user.require(Perm.PIPELINES_RUN)
            new_id = safe(
                lambda: ctx.dagster.reexecute(run_id, tags={"datahub/user": ctx.user.username}), None, "Relance"
            )
            if new_id:
                ctx.audit("run.reexecute", run_id, {"new_run_id": new_id})
                st.success(f"Nouveau run : `{new_id}`")
        if run["canTerminate"] and b2.button("⏹️ Arrêter le run", key="p01_terminate"):
            ctx.user.require(Perm.PIPELINES_RUN)
            safe(lambda: ctx.dagster.terminate(run_id), None, "Arrêt")
            ctx.audit("run.terminate", run_id)
            st.success("Arrêt demandé.")


def _custom_tab(ctx, jobs) -> None:
    st.markdown(
        "Créez un pipeline **sans écrire de code** : choisissez une sélection d'assets "
        '(syntaxe Dagster, ex. `group:marts_finance`, `key:"raw/orders"+` ou sélecteur dbt, ex. `tag:finance`) '
        "et, si besoin, une planification cron. Dagster le charge au rechargement de la code location."
    )
    if ctx.db is None:
        st.warning("Base applicative non configurée (DATAHUB_APP_DB_URL).")
        return
    pipelines = safe(ctx.db.list_pipelines, [], "Pipelines personnalisés")
    loaded = {j["name"] for j in jobs}
    if pipelines:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Nom": p["name"],
                        "Sélection": f"{p['selection_type']}: {p['selection']}",
                        "Cron": p["cron"] or "",
                        "Actif": p["enabled"],
                        "Chargé dans Dagster": "✅"
                        if f"custom_{p['name']}" in loaded
                        else ("—" if not p["enabled"] else "⚠️ rejeté"),
                        "Modifié par": p["updated_by"],
                    }
                    for p in pipelines
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    if not ctx.allowed(Perm.PIPELINES_EDIT):
        denied("pipelines.edit")
        return
    with st.form("p01_custom_form"):
        st.subheader("Créer / modifier un pipeline")
        name = st.text_input("Nom (minuscules, chiffres, _)", key="p01_c_name")
        description = st.text_input("Description", key="p01_c_desc")
        sel_type = st.radio("Langage de sélection", ["dagster", "dbt"], horizontal=True, key="p01_c_type")
        selection = st.text_input("Sélection", placeholder="group:marts_finance", key="p01_c_sel")
        cron = st.text_input("Planification cron (optionnel)", placeholder="0 7 * * 1-5", key="p01_c_cron")
        enabled = st.checkbox("Actif", value=True, key="p01_c_enabled")
        submitted = st.form_submit_button("💾 Enregistrer et recharger Dagster", type="primary")
    if submitted:
        ctx.user.require(Perm.PIPELINES_EDIT)
        try:
            saved = ctx.db.save_pipeline(
                {
                    "name": name,
                    "description": description,
                    "selection_type": sel_type,
                    "selection": selection,
                    "cron": cron,
                    "enabled": enabled,
                },
                ctx.user.username,
            )
            ctx.audit("pipeline.save", saved["name"], {"selection": selection, "cron": cron, "enabled": enabled})
            status = safe(ctx.dagster.reload_location, None, "Rechargement de Dagster")
            if status:
                st.success(f"Pipeline `{saved['name']}` enregistré ; code location : {status}.")
                st.rerun()
        except ValidationError as exc:
            st.error(str(exc))
    if pipelines:
        to_delete = st.selectbox("Supprimer un pipeline", [p["name"] for p in pipelines], key="p01_c_del")
        if st.button("🗑️ Supprimer", key="p01_c_del_btn"):
            ctx.user.require(Perm.PIPELINES_EDIT)
            ctx.db.delete_pipeline(to_delete)
            ctx.audit("pipeline.delete", to_delete)
            safe(ctx.dagster.reload_location, None, "Rechargement de Dagster")
            st.rerun()
