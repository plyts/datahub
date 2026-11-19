"""06. Logs & Error Management — centralized logs (Loki), run logs, error diagnosis and incidents."""

import time

import pandas as pd
import streamlit as st

from datahub_ui.clients.observability import build_logql
from datahub_ui.incidents import classify
from datahub_ui.rbac import Perm
from datahub_ui.ui import badge, fmt_ts, header, safe

LEVELS = ["", "error", "warn", "info", "debug"]


def render(ctx) -> None:
    header("06", "Logs & Error Management", "Logs centralisés, erreurs SQL / Python, diagnostic et incidents")
    tabs = st.tabs(["🚨 Erreurs récentes", "🧾 Logs d'un run", "📦 Logs des conteneurs (Loki)", "🎫 Incidents"])
    with tabs[0]:
        _recent_errors(ctx)
    with tabs[1]:
        _run_logs(ctx)
    with tabs[2]:
        _loki(ctx)
    with tabs[3]:
        _incidents(ctx)


def _failure_message(ctx, run_id: str) -> str:
    errors = safe(lambda: ctx.dagster.run_errors(run_id), [], "Erreurs du run")
    for event in errors:
        if event.get("error"):
            return f"{event['error'].get('className') or ''}: {event['error']['message']}".strip(": ")
    return errors[-1]["message"] if errors else ""


def _recent_errors(ctx) -> None:
    days = st.slider("Période (jours)", 1, 30, 7, key="p06_days")
    failed = safe(
        lambda: ctx.dagster.runs(limit=100, statuses=["FAILURE"], created_after=time.time() - days * 86400),
        [],
        "Runs en échec",
    )
    if not failed:
        st.success("Aucun échec sur la période. 🎉")
        return
    rows = []
    for run in failed[:30]:
        message = _failure_message(ctx, run["runId"])
        diag = classify(message)
        if ctx.db is not None:
            safe(
                lambda r=run, d=diag, m=message: ctx.db.record_incident(
                    r["runId"], r["jobName"], d.category, d.severity, m
                ),
                False,
                "Création d'incident",
            )
        rows.append(
            {
                "Date": fmt_ts(run["creationTime"]),
                "Pipeline": run["jobName"],
                "Catégorie": diag.category,
                "Sévérité": diag.severity,
                "Erreur": message[:300],
                "Piste": diag.hint,
                "Run": run["runId"],
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    by_category = pd.DataFrame(rows).groupby("Catégorie").size().rename("Échecs")
    st.bar_chart(by_category, height=220)


def _run_logs(ctx) -> None:
    runs = safe(lambda: ctx.dagster.runs(limit=50), [], "Runs")
    if not runs:
        st.info("Aucun run.")
        return
    labels = {
        f"{badge(r['status'])} {r['jobName']} · {fmt_ts(r['creationTime'])} · {r['runId'][:8]}": r["runId"]
        for r in runs
    }
    run_id = labels[st.selectbox("Run", list(labels), key="p06_run")]
    c1, c2 = st.columns(2)
    level = c1.selectbox("Niveau minimum", ["DEBUG", "INFO", "WARNING", "ERROR"], index=1, key="p06_level")
    text = c2.text_input("Filtrer le texte", key="p06_text").lower()
    events, _, _ = safe(lambda: ctx.dagster.run_events(run_id), ([], "", False), "Logs du run")
    order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    shown = [
        e
        for e in events
        if e.get("level") and order.index(e["level"]) >= order.index(level) and text in (e.get("message") or "").lower()
    ]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Heure": fmt_ts(int(e["timestamp"]) / 1000),
                    "Niveau": e["level"],
                    "Étape": e.get("stepKey") or "",
                    "Type": e.get("eventType") or e["__typename"],
                    "Message": (e.get("message") or "")[:500],
                }
                for e in shown
            ]
        ),
        hide_index=True,
        use_container_width=True,
        height=420,
    )
    for event in events:
        if event.get("error"):
            diag = classify(event["error"]["message"])
            with st.expander(f"❌ {event.get('stepKey') or 'run'} — {diag.category}", expanded=True):
                st.write(f"**Piste** : {diag.hint}")
                st.code(event["error"]["message"] + "\n" + "".join(event["error"].get("stack") or []), language="text")


def _loki(ctx) -> None:
    containers = safe(ctx.loki.labels, [], "Loki (labels)")
    c1, c2, c3, c4 = st.columns([2, 1, 2, 1])
    container = c1.selectbox("Conteneur", ["(tous)", *containers], key="p06_container")
    level = c2.selectbox("Niveau", LEVELS, key="p06_loki_level")
    contains = c3.text_input("Contient", key="p06_contains")
    minutes = c4.selectbox("Période", [15, 60, 360, 1440], index=1, format_func=lambda m: f"{m} min", key="p06_minutes")
    logql = build_logql(None if container == "(tous)" else container, contains or None, level or None)
    st.code(logql, language="text")
    st.button("🔄 Rafraîchir", key="p06_refresh")
    lines = safe(lambda: ctx.loki.query_range(logql, minutes=minutes, limit=500), [], "Requête Loki")
    if lines:
        st.dataframe(
            pd.DataFrame(
                [
                    {"Heure": fmt_ts(r["ts"]), "Conteneur": r.get("label_container", ""), "Ligne": r["line"][:1000]}
                    for r in lines
                ]
            ),
            hide_index=True,
            use_container_width=True,
            height=420,
        )
    else:
        st.info("Aucune ligne de log.")
    st.caption(f"Exploration avancée : [Grafana Explore]({ctx.settings.grafana_public_url}/explore)")


def _incidents(ctx) -> None:
    if ctx.db is None:
        st.warning("Base applicative non configurée.")
        return
    status = st.selectbox("Statut", ["open", "acknowledged", "resolved", "(tous)"], key="p06_inc_status")
    incidents = safe(lambda: ctx.db.list_incidents(None if status == "(tous)" else status), [], "Incidents")
    if not incidents:
        st.success("Aucun incident.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "#": i["id"],
                    "Statut": badge(i["status"]),
                    "Sévérité": i["severity"],
                    "Catégorie": i["category"],
                    "Pipeline": i["job_name"],
                    "Assigné": i["assignee"] or "",
                    "Créé": i["created_at"].strftime("%Y-%m-%d %H:%M"),
                    "Message": i["message"][:200],
                }
                for i in incidents
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    if not ctx.allowed(Perm.INCIDENTS_MANAGE):
        return
    with st.form("p06_incident_form"):
        incident_id = st.selectbox("Incident", [i["id"] for i in incidents])
        new_status = st.selectbox("Nouveau statut", ["acknowledged", "resolved", "open"])
        assignee = st.text_input("Assigné à", value=ctx.user.username)
        comment = st.text_area("Commentaire / cause racine")
        if st.form_submit_button("Mettre à jour", type="primary"):
            ctx.user.require(Perm.INCIDENTS_MANAGE)
            ctx.db.update_incident(int(incident_id), new_status, assignee, comment)
            ctx.audit("incident.update", str(incident_id), {"status": new_status})
            st.rerun()
