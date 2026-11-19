"""10. Administration & Security — service health, users & permissions, connections, secrets, audit trail."""

import pandas as pd
import streamlit as st

from datahub_ui.db.repository import CONNECTION_KINDS, ValidationError
from datahub_ui.health import check_all
from datahub_ui.rbac import Perm, Role
from datahub_ui.ui import badge, denied, fmt_ts, header, safe


def render(ctx) -> None:
    header(
        "10", "Administration & Security", "Santé, utilisateurs et permissions, connexions, secrets, journal d'audit"
    )
    tabs = st.tabs(
        ["❤️ Santé", "👥 Utilisateurs & rôles", "🔌 Connexions", "🔐 Secrets", "🧾 Journal d'audit", "⚙️ Administration"]
    )
    with tabs[0]:
        _health(ctx)
    with tabs[1]:
        _users(ctx)
    with tabs[2]:
        _connections(ctx)
    with tabs[3]:
        _secrets(ctx)
    with tabs[4]:
        _audit(ctx)
    with tabs[5]:
        _general(ctx)


def _health(ctx) -> None:
    results = check_all(ctx)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Service": h.name,
                    "État": "🟢 OK" if h.healthy else "🔴 KO",
                    "Latence (ms)": h.latency_ms,
                    "Détail": h.detail,
                }
                for h in results
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    targets = safe(ctx.prometheus.targets, [], "Cibles Prometheus")
    if targets:
        st.subheader("Cibles de collecte Prometheus")
        st.dataframe(pd.DataFrame(targets), hide_index=True, use_container_width=True)
    alerts = safe(ctx.prometheus.alerts, [], "Alertes")
    st.subheader(f"Alertes actives ({len(alerts)})")
    for alert in alerts:
        st.warning(
            f"**{alert['labels'].get('alertname')}** ({alert['labels'].get('severity', '')}) — "
            f"{alert.get('annotations', {}).get('summary', '')}"
        )


def _users(ctx) -> None:
    if not ctx.keycloak.configured:
        st.info("Compte de service Keycloak non configuré (KEYCLOAK_ADMIN_CLIENT_SECRET).")
        return
    search = st.text_input("Rechercher", key="p10_user_search")
    users = safe(lambda: ctx.keycloak.users(search), [], "Utilisateurs Keycloak")
    rows = []
    for u in users:
        roles = safe(lambda uid=u["id"]: ctx.keycloak.user_realm_roles(uid), [], "Rôles")
        rows.append(
            {
                "id": u["id"],
                "Utilisateur": u["username"],
                "Email": u.get("email", ""),
                "Actif": u.get("enabled", False),
                "Rôles Data Hub": ", ".join(r for r in roles if r in Role),
            }
        )
    st.dataframe(
        pd.DataFrame(rows).drop(columns=["id"]) if rows else pd.DataFrame(), hide_index=True, use_container_width=True
    )
    if not ctx.allowed(Perm.ADMIN_MANAGE):
        denied("admin.manage")
        return
    if not rows:
        return
    with st.form("p10_roles"):
        username = st.selectbox("Utilisateur", [r["Utilisateur"] for r in rows])
        user = next(r for r in rows if r["Utilisateur"] == username)
        current = [r for r in user["Rôles Data Hub"].split(", ") if r]
        roles = st.multiselect("Rôles", [r.value for r in Role], default=current)
        enabled = st.checkbox("Compte actif", value=user["Actif"])
        logout = st.checkbox("Fermer toutes ses sessions")
        if st.form_submit_button("Appliquer", type="primary"):
            ctx.user.require(Perm.ADMIN_MANAGE)
            if username == ctx.user.username and Role.ADMIN.value not in roles:
                st.error("Vous ne pouvez pas retirer votre propre rôle ADMIN.")
                return
            ctx.keycloak.set_datahub_roles(user["id"], set(roles))
            ctx.keycloak.set_enabled(user["id"], enabled)
            if logout:
                ctx.keycloak.logout_user(user["id"])
            ctx.audit("iam.user.update", username, {"roles": roles, "enabled": enabled, "logout": logout})
            st.success("Mis à jour (effectif à la prochaine connexion de l'utilisateur).")


def _connections(ctx) -> None:
    if ctx.db is None:
        st.warning("Base applicative non configurée.")
        return
    conns = safe(ctx.db.list_connections, [], "Connexions")
    if conns:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Nom": c["name"],
                        "Type": c["kind"],
                        "Hôte": c["host"],
                        "Port": c["port"],
                        "Base": c["database"],
                        "Utilisateur": c["username"],
                        "Secret (référence)": c["secret_ref"],
                        "Description": c["description"],
                    }
                    for c in conns
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    if not ctx.allowed(Perm.ADMIN_MANAGE):
        denied("admin.manage")
        return
    with st.form("p10_conn"):
        st.subheader("Ajouter / modifier une connexion")
        c1, c2 = st.columns(2)
        name = c1.text_input("Nom")
        kind = c2.selectbox("Type", CONNECTION_KINDS)
        host = c1.text_input("Hôte")
        port = c2.number_input("Port", 0, 65535, 5432)
        database = c1.text_input("Base / bucket / chemin")
        username = c2.text_input("Utilisateur")
        secret_ref = st.text_input("Référence du secret (NOM de variable d'environnement, ex. CRM_DB_PASSWORD)")
        description = st.text_input("Description")
        if st.form_submit_button("💾 Enregistrer", type="primary"):
            ctx.user.require(Perm.ADMIN_MANAGE)
            try:
                ctx.db.save_connection(
                    {
                        "name": name,
                        "kind": kind,
                        "host": host,
                        "port": int(port) or None,
                        "database": database,
                        "username": username,
                        "secret_ref": secret_ref,
                        "description": description,
                    },
                    ctx.user.username,
                )
                ctx.audit("connection.save", name, {"kind": kind, "host": host})
                st.rerun()
            except ValidationError as exc:
                st.error(str(exc))


def _secrets(ctx) -> None:
    st.markdown(
        "**Politique de gestion des secrets**\n"
        "- Les secrets sont dans `/opt/datahub/.env` (droits `600`, propriétaire `root`), générés par "
        "`scripts/generate-secrets.sh`, **jamais commités** (`.gitignore`).\n"
        "- Les services les reçoivent en variables d'environnement ; le Data Hub ne stocke que des **références** "
        "(nom de variable), jamais de valeur.\n"
        "- Rotation : `scripts/generate-secrets.sh --rotate <NOM>` puis `make deploy`.\n"
        "- Sauvegardes chiffrées avec `age`/`gpg` (voir docs/operations.md)."
    )
    if ctx.db is not None:
        refs = sorted({c["secret_ref"] for c in safe(ctx.db.list_connections, [], "Connexions") if c["secret_ref"]})
        st.write("Références de secrets utilisées :", ", ".join(f"`{r}`" for r in refs) or "aucune")


def _audit(ctx) -> None:
    if ctx.db is None:
        st.warning("Base applicative non configurée.")
        return
    c1, c2, c3 = st.columns(3)
    user = c1.text_input("Utilisateur", key="p10_audit_user")
    action = c2.text_input("Action (préfixe, ex. pipeline.)", key="p10_audit_action")
    limit = c3.number_input("Lignes", 10, 5000, 200, key="p10_audit_limit")
    rows = safe(lambda: ctx.db.list_audit(int(limit), user or None, action or None), [], "Journal d'audit")
    df = pd.DataFrame(
        [
            {
                "Date": r["ts"].strftime("%Y-%m-%d %H:%M:%S"),
                "Utilisateur": r["username"],
                "Rôles": ", ".join(r["roles"]),
                "Action": r["action"],
                "Cible": r["target"],
                "Succès": badge("SUCCESS" if r["success"] else "FAILURE"),
                "Détails": str(r["details"]),
            }
            for r in rows
        ]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)
    if not df.empty:
        st.download_button("⬇️ Exporter (CSV)", df.to_csv(index=False).encode("utf-8"), "audit_log.csv", "text/csv")


def _general(ctx) -> None:
    daemons = safe(ctx.dagster.daemon_health, [], "Daemons Dagster")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Daemon": d["daemonType"],
                    "Santé": "🟢" if d["healthy"] else "🔴",
                    "Requis": d["required"],
                    "Dernier heartbeat": fmt_ts(d["lastHeartbeatTime"]),
                }
                for d in daemons
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    if ctx.allowed(Perm.ADMIN_MANAGE) and st.button("🔄 Recharger la code location Dagster", key="p10_reload"):
        ctx.user.require(Perm.ADMIN_MANAGE)
        status = safe(ctx.dagster.reload_location, None, "Rechargement")
        if status:
            ctx.audit("dagster.reload", ctx.settings.dagster_location)
            st.success(f"Code location rechargée : {status}")
    st.markdown(
        f"- [Console d'administration Keycloak]({ctx.settings.keycloak_public_url}/admin/{ctx.settings.keycloak_realm}/console/)\n"
        f"- [Grafana]({ctx.settings.grafana_public_url}) · [Dagster]({ctx.settings.dagster_public_url})"
    )
