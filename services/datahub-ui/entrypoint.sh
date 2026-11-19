#!/usr/bin/env bash
# Data Hub UI entrypoint: render Streamlit OIDC secrets from the environment,
# apply database migrations, then start Streamlit.
set -euo pipefail

mkdir -p "${HOME}/.streamlit"
if [[ "${DATAHUB_AUTH_MODE:-oidc}" == "oidc" ]]; then
  : "${OIDC_CLIENT_SECRET:?OIDC_CLIENT_SECRET is required}"
  : "${STREAMLIT_COOKIE_SECRET:?STREAMLIT_COOKIE_SECRET is required}"
  umask 077
  cat > "${HOME}/.streamlit/secrets.toml" <<TOML
[auth]
redirect_uri = "${DATAHUB_PUBLIC_URL}/oauth2callback"
cookie_secret = "${STREAMLIT_COOKIE_SECRET}"

[auth.keycloak]
client_id = "${OIDC_CLIENT_ID:-datahub-ui}"
client_secret = "${OIDC_CLIENT_SECRET}"
server_metadata_url = "${DATAHUB_PUBLIC_URL}/auth/realms/${KEYCLOAK_REALM:-datahub}/.well-known/openid-configuration"
client_kwargs = { scope = "openid profile email", prompt = "login" }
TOML
fi

if [[ -n "${DATAHUB_APP_DB_URL:-}" ]]; then
  for attempt in $(seq 1 30); do
    if python -m datahub_ui.db.migrate; then break; fi
    echo "Database not ready (attempt ${attempt}/30), retrying in 2s..." >&2
    sleep 2
  done
fi

exec streamlit run streamlit_app.py \
  --server.port="${STREAMLIT_PORT:-8501}" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --browser.gatherUsageStats=false \
  --server.enableXsrfProtection=true \
  "$@"
