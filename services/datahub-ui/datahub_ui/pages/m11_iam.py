"""11. Identity & Access Management — Keycloak SSO / OIDC, RBAC, groups and sessions."""

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from datahub_ui.rbac import ROLE_DESCRIPTIONS, ROLE_PERMISSIONS, Perm, Role
from datahub_ui.ui import header, safe

SAFE_CLAIMS = (
    "iss",
    "aud",
    "azp",
    "preferred_username",
    "email",
    "email_verified",
    "name",
    "roles",
    "groups",
    "auth_time",
    "iat",
    "exp",
    "acr",
    "sid",
)


def _ts(value) -> str:
    return datetime.fromtimestamp(int(value), tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC") if value else "—"


def render(ctx) -> None:
    s = ctx.settings
    header("11", "Identity & Access Management", "Keycloak — SSO OpenID Connect, RBAC, groupes et sessions")
    user = ctx.user
    tabs = st.tabs(["👤 Mon profil", "🧭 Matrice RBAC", "🏢 Realm & sessions"])
    with tabs[0]:
        c1, c2 = st.columns(2)
        c1.write(f"**Utilisateur** : {user.username}")
        c1.write(f"**Nom** : {user.name or '—'}")
        c1.write(f"**Email** : {user.email or '—'}")
        c2.write(f"**Rôles** : {', '.join(sorted(r.value for r in user.roles)) or 'aucun'}")
        c2.write(f"**Groupes** : {', '.join(user.groups) or '—'}")
        c2.write(
            f"**Mode d'authentification** : {s.auth_mode.upper()} ({'Keycloak OIDC' if s.auth_mode == 'oidc' else 'développement'})"
        )
        claims = user.claims
        if claims:
            st.write(
                f"**Connexion** : {_ts(claims.get('auth_time') or claims.get('iat'))} — "
                f"**expiration du jeton** : {_ts(claims.get('exp'))}"
            )
            with st.expander("Claims du jeton d'identité (OIDC)"):
                st.json({k: claims[k] for k in SAFE_CLAIMS if k in claims})
        st.markdown(f"[Gérer mon compte Keycloak (mot de passe, MFA, sessions)]({s.keycloak_account_url})")
        st.dataframe(
            pd.DataFrame([{"Permission": p.value, "Accordée": "✅" if user.can(p) else "—"} for p in Perm]),
            hide_index=True,
            use_container_width=True,
            height=300,
        )
    with tabs[1]:
        matrix = pd.DataFrame(
            [{"Permission": p.value, **{r.value: "✅" if p in ROLE_PERMISSIONS[r] else "" for r in Role}} for p in Perm]
        )
        st.dataframe(matrix, hide_index=True, use_container_width=True, height=560)
        st.dataframe(
            pd.DataFrame([{"Rôle": r.value, "Droits": d} for r, d in ROLE_DESCRIPTIONS.items()]), hide_index=True
        )
        st.caption(
            "Les rôles sont des *realm roles* Keycloak, attribués directement ou via les groupes "
            "(/admins, /data-engineers, /analysts, /viewers). Chaque action sensible est revérifiée côté "
            "serveur avant l'appel au service et tracée dans le journal d'audit."
        )
    with tabs[2]:
        if not ctx.allowed(Perm.IAM_MANAGE):
            st.info("🔒 Réservé aux administrateurs.")
            return
        if not ctx.keycloak.configured:
            st.info("Compte de service Keycloak non configuré.")
            return
        c1, c2, c3 = st.columns(3)
        users = safe(lambda: ctx.keycloak.users(), [], "Utilisateurs")
        c1.metric("Utilisateurs", len(users))
        c2.metric(
            "Sessions actives (datahub-ui)",
            safe(lambda: ctx.keycloak.client_sessions_count("datahub-ui"), 0, "Sessions"),
        )
        groups = safe(ctx.keycloak.groups, [], "Groupes")
        c3.metric("Groupes", len(groups))
        st.dataframe(
            pd.DataFrame([{"Groupe": g["path"], "Sous-groupes": g.get("subGroupCount", 0)} for g in groups]),
            hide_index=True,
        )
        st.markdown(f"[Console d'administration du realm]({s.keycloak_public_url}/admin/{s.keycloak_realm}/console/)")
