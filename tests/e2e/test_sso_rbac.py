"""SSO (Keycloak OIDC) and RBAC through the real entry point."""

import json
import os
import time

import pytest
from authlib.jose import jwt

from tests.e2e.conftest import PUBLIC_URL, USERS, keycloak_login, new_session


def streamlit_login_url() -> str:
    """URL produced by st.login("keycloak"): /auth/login?provider=<signed provider token>."""
    secret = os.environ["STREAMLIT_COOKIE_SECRET"]
    token = jwt.encode({"alg": "HS256"}, {"provider": "keycloak", "exp": int(time.time()) + 120}, secret)
    return f"{PUBLIC_URL}/auth/login?provider={token.decode()}"


def decode_streamlit_user(session) -> dict:
    from streamlit.web.server.starlette.starlette_app_utils import decode_signed_value

    raw = session.cookies.get("_streamlit_user")
    assert raw, f"no Streamlit auth cookie, cookies={list(session.cookies.keys())}"
    value = decode_signed_value(os.environ["STREAMLIT_COOKIE_SECRET"], "_streamlit_user", raw)
    assert value, "auth cookie signature invalid"
    return json.loads(value)


def test_oidc_discovery_uses_public_issuer():
    config = new_session().get(f"{PUBLIC_URL}/auth/realms/datahub/.well-known/openid-configuration", timeout=10).json()
    assert config["issuer"] == f"{PUBLIC_URL}/auth/realms/datahub"
    assert config["authorization_endpoint"].startswith(f"{PUBLIC_URL}/auth/")
    assert "S256" in config["code_challenge_methods_supported"]


@pytest.mark.parametrize("role", list(USERS))
def test_streamlit_sso_login_returns_datahub_roles(role):
    session = new_session()
    final = keycloak_login(session, streamlit_login_url(), USERS[role])
    assert final.status_code == 200 and final.url.rstrip("/") == PUBLIC_URL, final.url
    user = decode_streamlit_user(session)
    assert user["preferred_username"] == USERS[role]
    assert role in user["roles"], user
    assert user["iss"] == f"{PUBLIC_URL}/auth/realms/datahub"


def test_wrong_password_is_rejected():
    session = new_session()
    final = keycloak_login(session, streamlit_login_url(), USERS["ADMIN"], password="wrong-Passw0rd")
    assert "_streamlit_user" not in session.cookies
    assert "kc-form-login" in final.text  # login page shown again with an error


def test_dagster_ui_requires_authentication():
    response = new_session().get(f"{PUBLIC_URL}/dagster/", allow_redirects=False, timeout=10)
    assert response.status_code == 302
    assert "/oauth2/start" in response.headers["Location"]


def test_dagster_graphql_is_not_reachable_anonymously():
    response = new_session().post(f"{PUBLIC_URL}/dagster/graphql", json={"query": "{ version }"},
                                  allow_redirects=False, timeout=10)
    assert response.status_code in (302, 401)


@pytest.mark.parametrize(("role", "allowed"), [("ADMIN", True), ("DATA_ENGINEER", True), ("ANALYST", False),
                                               ("VIEWER", False)])
def test_dagster_ui_rbac(role, allowed):
    session = new_session()
    final = keycloak_login(session, f"{PUBLIC_URL}/oauth2/start?rd={PUBLIC_URL}/dagster/", USERS[role])
    if allowed:
        assert final.status_code == 200, (final.status_code, final.url)
        assert "Dagster" in final.text
        graphql = session.post(f"{PUBLIC_URL}/dagster/graphql", json={"query": "{ version }"}, timeout=10)
        assert graphql.status_code == 200 and graphql.json()["data"]["version"]
    else:
        assert final.status_code == 403, (final.status_code, final.url)


def test_dbt_docs_are_protected():
    response = new_session().get(f"{PUBLIC_URL}/dbt-docs/", allow_redirects=False, timeout=10)
    assert response.status_code == 302


def test_security_headers():
    response = new_session().get(f"{PUBLIC_URL}/_stcore/health", timeout=10)
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "Server" not in response.headers
