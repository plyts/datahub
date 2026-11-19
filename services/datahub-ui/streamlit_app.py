"""Data Hub — Streamlit entry point (`streamlit run streamlit_app.py`)."""

import logging

import streamlit as st

from datahub_ui.auth import OIDC_PROVIDER, current_user
from datahub_ui.config import get_settings
from datahub_ui.context import build_context
from datahub_ui.navigation import render_function, visible_pages
from datahub_ui.rbac import PermissionDenied, Role
from datahub_ui.telemetry import setup_tracing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
st.set_page_config(page_title="Data Hub", page_icon="🛰️", layout="wide")
setup_tracing("datahub-ui")

settings = get_settings()
errors = settings.validate()
if errors:
    for error in errors:
        st.error(error)
    st.stop()

user = current_user(settings)
if user is None:
    st.title("🛰️ Data Hub")
    st.write("Plateforme d'orchestration, de transformation et d'observabilité des données.")
    st.button("🔐 Se connecter avec Keycloak (SSO)", type="primary", on_click=st.login, args=[OIDC_PROVIDER])
    st.stop()

if not user.roles:
    st.error(f"Bonjour {user.username} : aucun rôle Data Hub ne vous est attribué. Contactez un administrateur.")
    if settings.auth_mode == "oidc":
        st.button("Se déconnecter", on_click=st.logout)
    st.stop()

ctx = build_context(settings, user)

with st.sidebar:
    env_color = {"dev": "green", "staging": "orange", "prod": "red"}[settings.env]
    st.markdown(f"### 🛰️ Data Hub  :{env_color}-badge[{settings.env.upper()}]")
    st.write(f"👤 **{user.name or user.username}**")
    st.caption(" · ".join(sorted(r.value for r in user.roles)))
    if settings.auth_mode == "none":
        st.selectbox(
            "Rôle (mode dev)",
            [r.value for r in Role],
            key="dev_role",
            index=[r.value for r in Role].index(settings.dev_default_role),
        )
    else:
        st.button("Se déconnecter", on_click=st.logout, use_container_width=True)

pages: dict[str, list] = {}
for spec in visible_pages(user):
    render = render_function(spec)
    pages.setdefault(spec.section, []).append(
        st.Page(lambda r=render: r(ctx), title=spec.title, icon=spec.icon, url_path=spec.url_path)
    )

navigation = st.navigation(pages)
try:
    navigation.run()
except PermissionDenied as exc:
    ctx.audit("access.denied", str(exc.perm), success=False)
    st.error(f"⛔ {exc}")
