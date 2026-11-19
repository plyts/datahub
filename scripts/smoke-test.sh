#!/usr/bin/env bash
# Smoke tests through the public entry point (run after every deployment).
set -euo pipefail
cd "$(dirname "$0")/.."
ENV="${1:-dev}"
set -a
# shellcheck source=/dev/null
source "envs/${ENV}.env"
set +a
URL="${DATAHUB_PUBLIC_URL}"
CURL=(curl -sk --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 15)
fail=0
check() {  # name, url, expected codes (regex)
  local code; code="$("${CURL[@]}" "$2" || true)"
  if [[ "$code" =~ ^($3)$ ]]; then printf '  ✔ %-28s %s\n' "$1" "$code"; else printf '  ✘ %-28s %s (expected %s)\n' "$1" "$code" "$3"; fail=1; fi
}
echo "Smoke tests on ${URL}"
check "Data Hub (Streamlit)"        "${URL}/_stcore/health"                                        "200"
check "Keycloak OIDC discovery"     "${URL}/auth/realms/${KEYCLOAK_REALM:-datahub}/.well-known/openid-configuration" "200"
check "Dagster UI protected"        "${URL}/dagster/"                                              "302"
check "dbt docs protected"          "${URL}/dbt-docs/"                                             "302"
check "oauth2-proxy"                "${URL}/oauth2/start?rd=/dagster/"                             "302"
check "Grafana"                     "${URL}/grafana/api/health"                                    "200"
exit $fail
