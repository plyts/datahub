"""07. Backfill & Recovery — partition backfills, retries, re-execution from failure."""

import time
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from datahub_ui.rbac import Perm
from datahub_ui.ui import badge, denied, fmt_ts, header, safe


def missing_partitions(all_keys: list[str], ranges: list[dict], include_failed: bool = True) -> list[str]:
    """Partitions not materialized yet (failed ones are included unless include_failed=False).

    Daily partition keys are ISO dates, so lexical comparison follows chronological order.
    """
    done: set[str] = set()
    for r in ranges:
        if r["status"] in ("MATERIALIZED", "MATERIALIZING") or (r["status"] == "FAILED" and not include_failed):
            done.update(k for k in all_keys if r["startKey"] <= k <= r["endKey"])
    return [k for k in all_keys if k not in done]


def render(ctx) -> None:
    header(
        "07", "Backfill & Recovery", "Recalcul de partitions historiques, reprise des données manquantes et relances"
    )
    tabs = st.tabs(["🧱 Backfill de partitions", "📋 Backfills", "🔁 Relances après échec", "ℹ️ Incrémental"])
    with tabs[0]:
        _backfill(ctx)
    with tabs[1]:
        _backfills(ctx)
    with tabs[2]:
        _retries(ctx)
    with tabs[3]:
        st.markdown(
            "- **Assets d'ingestion** : chaque partition est supprimée puis réinsérée → toute relance est idempotente.\n"
            "- **`marts/fct_orders`** : modèle dbt incrémental (`delete+insert` sur `order_id`) ; Dagster passe la "
            "fenêtre `[min_date, max_date)` de la partition (ou de la plage en backfill).\n"
            "- **BackfillPolicy single_run** : un backfill d'une plage de jours s'exécute en **un seul run**.\n"
            "- **Retries** : 3 tentatives avec backoff exponentiel sur l'ingestion, 1 sur dbt, et relance "
            "automatique des runs tués par l'infrastructure (`run_retries`)."
        )


def _backfill(ctx) -> None:
    assets = [a for a in safe(ctx.dagster.assets, [], "Assets") if a["partitioned"]]
    if not assets:
        st.info("Aucun asset partitionné.")
        return
    keys = [a["key"] for a in assets]
    selection = st.multiselect("Assets à recalculer", keys, default=keys, key="p07_assets")
    if not selection:
        return
    all_keys = safe(lambda: ctx.dagster.partition_keys(selection[0]), [], "Partitions")
    ranges = safe(lambda: ctx.dagster.partition_ranges(selection[0]), [], "Statut des partitions")
    missing = missing_partitions(all_keys, ranges)
    c1, c2, c3 = st.columns(3)
    c1.metric("Partitions", len(all_keys))
    c2.metric("Manquantes / en échec", len(missing))
    c3.metric("Couverture", f"{100 * (1 - len(missing) / len(all_keys)):.1f} %" if all_keys else "—")

    mode = st.radio(
        "Partitions", ["Plage de dates", "Uniquement les manquantes / en échec"], horizontal=True, key="p07_mode"
    )
    if mode == "Plage de dates":
        c1, c2 = st.columns(2)
        start = c1.date_input("Du", value=date.today() - timedelta(days=7), key="p07_start")
        end = c2.date_input("Au (inclus)", value=date.today() - timedelta(days=1), key="p07_end")
        partitions = [k for k in all_keys if start.isoformat() <= k <= end.isoformat()]
    else:
        partitions = missing
    st.write(
        f"**{len(partitions)} partition(s)** sélectionnée(s)"
        + (f" : `{partitions[0]}` → `{partitions[-1]}`" if partitions else "")
    )
    if not ctx.allowed(Perm.BACKFILL_RUN):
        denied("backfill.run")
        return
    include_downstream = st.checkbox("Inclure les modèles dbt en aval (staging → marts)", value=True, key="p07_down")
    confirm = st.checkbox(f"Je confirme le recalcul de {len(partitions)} partition(s)", key="p07_confirm")
    if st.button("🚀 Lancer le backfill", type="primary", disabled=not (partitions and confirm), key="p07_launch"):
        ctx.user.require(Perm.BACKFILL_RUN)
        targets = list(selection)
        if include_downstream:
            graph_assets = safe(ctx.dagster.assets, [], "Assets")
            down = {d for a in graph_assets if a["key"] in selection for d in a["dependents"]}
            frontier = set(down)
            while frontier:
                frontier = {d for a in graph_assets if a["key"] in frontier for d in a["dependents"]} - down
                down |= frontier
            targets = sorted(set(targets) | down)
        backfill_id = safe(
            lambda: ctx.dagster.launch_backfill(
                targets,
                partition_names=partitions,
                title=f"Backfill {partitions[0]} → {partitions[-1]}",
                tags={"datahub/user": ctx.user.username},
            ),
            None,
            "Backfill",
        )
        if backfill_id:
            ctx.audit("backfill.launch", backfill_id, {"assets": targets, "partitions": len(partitions)})
            st.success(f"Backfill `{backfill_id}` lancé ({len(partitions)} partitions, {len(targets)} assets).")


def _backfills(ctx) -> None:
    backfills = safe(ctx.dagster.backfills, [], "Backfills")
    if not backfills:
        st.info("Aucun backfill.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "ID": b["id"],
                    "Statut": badge(b["status"]),
                    "Titre": b["title"] or "",
                    "Partitions": b["numPartitions"],
                    "Assets": len(b["assets"]),
                    "Créé": fmt_ts(b["timestamp"]),
                    "Par": b["user"] or "",
                    "Erreur": b["error"] or "",
                }
                for b in backfills
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    running = [b["id"] for b in backfills if b["status"] == "REQUESTED"]
    if running and ctx.allowed(Perm.BACKFILL_RUN):
        bid = st.selectbox("Backfill en cours", running, key="p07_cancel_id")
        if st.button("⏹️ Annuler", key="p07_cancel"):
            ctx.user.require(Perm.BACKFILL_RUN)
            safe(lambda: ctx.dagster.cancel_backfill(bid), None, "Annulation")
            ctx.audit("backfill.cancel", bid)
            st.rerun()


def _retries(ctx) -> None:
    failed = safe(
        lambda: ctx.dagster.runs(limit=50, statuses=["FAILURE"], created_after=time.time() - 14 * 86400),
        [],
        "Runs en échec",
    )
    if not failed:
        st.success("Aucun run en échec sur 14 jours.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Pipeline": r["jobName"],
                    "Partition": r["tags"].get("dagster/partition", ""),
                    "Date": fmt_ts(r["creationTime"]),
                    "Run": r["runId"],
                }
                for r in failed
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    if not ctx.allowed(Perm.PIPELINES_RUN):
        denied("pipelines.run")
        return
    run_id = st.selectbox("Run à relancer", [r["runId"] for r in failed], key="p07_retry_run")
    strategy = st.radio(
        "Stratégie",
        ["FROM_FAILURE", "ALL_STEPS"],
        horizontal=True,
        key="p07_strategy",
        format_func=lambda s: "Depuis l'étape en échec" if s == "FROM_FAILURE" else "Toutes les étapes",
    )
    if st.button("🔁 Relancer", type="primary", key="p07_retry"):
        ctx.user.require(Perm.PIPELINES_RUN)
        new_id = safe(
            lambda: ctx.dagster.reexecute(run_id, strategy, {"datahub/user": ctx.user.username}), None, "Relance"
        )
        if new_id:
            ctx.audit("run.reexecute", run_id, {"strategy": strategy, "new_run_id": new_id})
            st.success(f"Nouveau run `{new_id}`")
