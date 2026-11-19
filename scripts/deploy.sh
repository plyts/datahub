#!/usr/bin/env bash
# Deployment on the VM: validate -> backup -> build -> up -> wait healthy -> smoke tests.
#   scripts/deploy.sh prod
set -euo pipefail
cd "$(dirname "$0")/.."
ENV="${1:-dev}"
[[ -f .env ]] || { echo ".env missing: run make secrets" >&2; exit 1; }
COMPOSE=(docker compose --env-file "envs/${ENV}.env" --env-file .env -f docker-compose.yml -f "${COMPOSE_OVERRIDE:-compose/docker-compose.${ENV}.yml}")
VERSION="$(git describe --tags --always 2>/dev/null || echo unknown)"
DATAHUB_GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
export DATAHUB_GIT_SHA

echo "==> Validating configuration (${ENV}, ${VERSION})"
"${COMPOSE[@]}" config -q
if "${COMPOSE[@]}" ps --status running --quiet postgres | grep -q .; then
  echo "==> Backup before deployment"
  ./scripts/backup.sh "$ENV"
fi
echo "==> Building images"
"${COMPOSE[@]}" build
echo "==> Starting services"
"${COMPOSE[@]}" up -d --remove-orphans
./scripts/wait-healthy.sh "$ENV"
echo "==> Smoke tests"
./scripts/smoke-test.sh "$ENV"
mkdir -p backups
echo "$(date -Iseconds) ${ENV} ${VERSION} ${DATAHUB_GIT_SHA}" >> backups/deploy-history.log
echo "Deployed ${VERSION} on ${ENV}."
