#!/usr/bin/env bash
# Wait until every service with a healthcheck is healthy (or fail after TIMEOUT seconds).
set -euo pipefail
cd "$(dirname "$0")/.."
ENV="${1:-dev}"
TIMEOUT="${TIMEOUT:-600}"
COMPOSE=(docker compose --env-file "envs/${ENV}.env" --env-file .env -f docker-compose.yml -f "${COMPOSE_OVERRIDE:-compose/docker-compose.${ENV}.yml}")
deadline=$((SECONDS + TIMEOUT))
while (( SECONDS < deadline )); do
  states="$("${COMPOSE[@]}" ps --all --format '{{.Service}} {{.State}} {{.Health}}')"
  # Ready = running, and healthy when the service defines a healthcheck. A container in a
  # restart loop ("restarting") is never considered ready.
  pending="$(awk '$2 != "running" || ($3 != "" && $3 != "healthy") {print $1"("$2($3 != "" ? "/"$3 : "")")"}' <<<"$states")"
  exited="$(awk '$2 == "dead" {print $1}' <<<"$states")"
  if [[ -n "$exited" ]]; then echo "Stopped services: $exited" >&2; "${COMPOSE[@]}" ps; exit 1; fi
  if [[ -z "$pending" ]]; then echo "All services healthy."; "${COMPOSE[@]}" ps --format 'table {{.Service}}\t{{.Status}}'; exit 0; fi
  echo "Waiting for: $pending"
  sleep 10
done
echo "Timeout after ${TIMEOUT}s" >&2
"${COMPOSE[@]}" ps
exit 1
