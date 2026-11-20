"""Keycloak realm consistency with the application RBAC, secrets hygiene, entry point routing."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "datahub-ui"))
from datahub_ui.rbac import Role  # noqa: E402

REALM = json.loads((ROOT / "services/keycloak/realm/datahub-realm.json").read_text())
CLIENTS = {c["clientId"]: c for c in REALM["clients"]}


def test_realm_roles_match_application_roles():
    assert {r["name"] for r in REALM["roles"]["realm"]} == {r.value for r in Role}


def test_groups_grant_one_role_each():
    assert {g["path"]: g["realmRoles"] for g in REALM["groups"]} == {
        "/admins": ["ADMIN"], "/data-engineers": ["DATA_ENGINEER"], "/analysts": ["ANALYST"], "/viewers": ["VIEWER"]}


def test_no_plaintext_secret_in_realm():
    for client in REALM["clients"]:
        assert re.fullmatch(r"\$\{[A-Z_]+\}", client["secret"]), client["clientId"]
    for user in REALM["users"]:
        for cred in user.get("credentials", []):
            assert re.fullmatch(r"\$\{[A-Z_]+\}", cred["value"])


def test_interactive_clients_are_hardened():
    for name in ("datahub-ui", "dagster-proxy", "grafana"):
        client = CLIENTS[name]
        assert client["publicClient"] is False
        assert client["directAccessGrantsEnabled"] is False and client["implicitFlowEnabled"] is False
        assert client["attributes"]["pkce.code.challenge.method"] == "S256"
        assert all(uri.startswith("${DATAHUB_PUBLIC_URL}/") for uri in client["redirectUris"])
        mapper = next(m for m in client["protocolMappers"] if m["name"] == "realm roles")
        assert mapper["config"]["claim.name"] == "roles"
        assert mapper["config"]["id.token.claim"] == "true" and mapper["config"]["userinfo.token.claim"] == "true"


def test_redirect_uris_match_the_services():
    assert CLIENTS["datahub-ui"]["redirectUris"] == ["${DATAHUB_PUBLIC_URL}/oauth2callback"]
    assert CLIENTS["dagster-proxy"]["redirectUris"] == ["${DATAHUB_PUBLIC_URL}/oauth2/callback"]
    assert CLIENTS["grafana"]["redirectUris"] == ["${DATAHUB_PUBLIC_URL}/grafana/login/generic_oauth"]


def test_admin_service_account_least_privilege():
    admin = CLIENTS["datahub-admin-api"]
    assert admin["serviceAccountsEnabled"] and not admin["standardFlowEnabled"]
    assert "kc-admin-api" in admin["defaultClientScopes"]  # required by Keycloak >= 26.x for the admin REST API
    sa = next(u for u in REALM["users"] if u.get("serviceAccountClientId") == "datahub-admin-api")
    roles = set(sa["clientRoles"]["realm-management"])
    assert "manage-users" in roles and "realm-admin" not in roles and "manage-realm" not in roles


def test_realm_security_policies():
    assert REALM["bruteForceProtected"] is True
    assert REALM["registrationAllowed"] is False
    assert "length(12)" in REALM["passwordPolicy"]
    assert REALM["sslRequired"] == "external"


def test_no_env_file_is_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert not [f for f in tracked if re.search(r"(^|/)\.env($|\.(?!example))", f)], "a .env file is tracked"


def test_generated_secrets(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/generate-secrets.sh").write_bytes((ROOT / "scripts/generate-secrets.sh").read_bytes())
    os.chmod(tmp_path / "scripts/generate-secrets.sh", 0o755)
    (tmp_path / ".env.example").write_text((ROOT / ".env.example").read_text())
    subprocess.run(["./scripts/generate-secrets.sh"], cwd=tmp_path, check=True, capture_output=True)
    env = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text().splitlines()
               if "=" in line and not line.startswith("#"))
    assert oct((tmp_path / ".env").stat().st_mode)[-3:] == "600"
    assert not [k for k, v in env.items() if v.startswith("CHANGE_ME")]
    assert len(env["OAUTH2_PROXY_COOKIE_SECRET"]) == 32
    password = env["DATAHUB_ADMIN_PASSWORD"]
    assert len(password) >= 12 and re.search(r"[A-Z]", password) and re.search(r"[a-z]", password) and re.search(r"\d", password)
    assert len(set(env[k] for k in env if k.endswith("PASSWORD") and k != "SMTP_PORT")) > 5  # all different
    refused = subprocess.run(["./scripts/generate-secrets.sh"], cwd=tmp_path, capture_output=True)
    assert refused.returncode != 0, "must refuse to overwrite an existing .env"


def test_caddy_routes_streamlit_auth_before_keycloak():
    caddyfile = (ROOT / "services/caddy/Caddyfile").read_text()
    assert caddyfile.index("handle /auth/login") < caddyfile.index("handle /auth*")
    assert "handle /auth/logout" in caddyfile
    dagster_block = caddyfile[caddyfile.index("handle /dagster*"):caddyfile.index("redir /dbt-docs")]
    assert "import protected" in dagster_block


# --- HTTPS in production: server-to-server OIDC calls never go through the public TLS endpoint.
# With DATAHUB_PUBLIC_URL=https://... and Caddy's internal CA, containers do not trust the
# certificate; browsers use the public URL, back-channel calls use http://keycloak:8080.
KEYCLOAK_INTERNAL = "http://keycloak:8080/auth/realms/${KEYCLOAK_REALM:-datahub}/protocol/openid-connect"


def _compose_services():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def test_keycloak_resolves_backchannel_urls_dynamically():
    env = _compose_services()["keycloak"]["environment"]
    assert env["KC_HOSTNAME"] == "${DATAHUB_PUBLIC_URL:?}/auth"   # issuer and browser URLs stay public
    assert env["KC_HOSTNAME_BACKCHANNEL_DYNAMIC"] == "true"


def test_oauth2_proxy_uses_the_public_login_and_internal_backchannel():
    env = _compose_services()["oauth2-proxy"]["environment"]
    assert env["OAUTH2_PROXY_SKIP_OIDC_DISCOVERY"] == "true"
    assert env["OAUTH2_PROXY_OIDC_ISSUER_URL"].startswith("${DATAHUB_PUBLIC_URL:?}/")
    assert env["OAUTH2_PROXY_LOGIN_URL"].startswith("${DATAHUB_PUBLIC_URL:?}/")
    for key, suffix in [("REDEEM_URL", "/token"), ("OIDC_JWKS_URL", "/certs"), ("VALIDATE_URL", "/userinfo")]:
        assert env[f"OAUTH2_PROXY_{key}"] == KEYCLOAK_INTERNAL + suffix


def test_grafana_redeems_tokens_on_the_internal_url():
    env = _compose_services()["grafana"]["environment"]
    assert env["GF_AUTH_GENERIC_OAUTH_AUTH_URL"].startswith("${DATAHUB_PUBLIC_URL:?}/")
    assert env["GF_AUTH_GENERIC_OAUTH_TOKEN_URL"] == KEYCLOAK_INTERNAL + "/token"
    assert env["GF_AUTH_GENERIC_OAUTH_API_URL"] == KEYCLOAK_INTERNAL + "/userinfo"


def test_streamlit_discovers_oidc_on_the_internal_url():
    entrypoint = (ROOT / "services/datahub-ui/entrypoint.sh").read_text()
    assert 'server_metadata_url = "${KEYCLOAK_INTERNAL_URL:-${DATAHUB_PUBLIC_URL}/auth}/realms/' in entrypoint
    assert _compose_services()["datahub-ui"]["environment"]["KEYCLOAK_INTERNAL_URL"] == "http://keycloak:8080/auth"


def test_tls_mode_selects_automatic_or_company_certificates():
    caddyfile = (ROOT / "services/caddy/Caddyfile").read_text()
    assert "import tls_{$DATAHUB_TLS_MODE:auto}" in caddyfile
    assert "(tls_auto) {" in caddyfile
    assert "tls /etc/caddy/certs/fullchain.pem /etc/caddy/certs/privkey.pem" in caddyfile
    caddy = _compose_services()["caddy"]
    assert "${DATAHUB_TLS_DIR:-./certs}:/etc/caddy/certs:ro" in caddy["volumes"]
    assert caddy["environment"]["DATAHUB_TLS_MODE"] == "${DATAHUB_TLS_MODE:-auto}"


def test_private_keys_are_never_committed():
    ignored = subprocess.run(["git", "check-ignore", "certs/privkey.pem", "certs/fullchain.pem"], cwd=ROOT,
                             capture_output=True, text=True, check=False).stdout.split()
    assert ignored == ["certs/privkey.pem", "certs/fullchain.pem"]
    assert subprocess.run(["git", "check-ignore", "-q", "certs/.gitkeep"], cwd=ROOT, check=False).returncode == 1
