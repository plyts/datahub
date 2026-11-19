#!/usr/bin/env bash
# Roll back to a previous Git tag (code + images); data is kept. A backup is taken first.
#   scripts/rollback.sh prod v1.0.0
set -euo pipefail
cd "$(dirname "$0")/.."
ENV="${1:?env}"; TAG="${2:?tag, e.g. v1.0.0}"
git fetch --tags --quiet || true
git rev-parse --verify --quiet "refs/tags/${TAG}" >/dev/null || { echo "Unknown tag ${TAG}" >&2; exit 1; }
echo "Current: $(git describe --tags --always) -> rollback to ${TAG}"
git checkout --quiet "${TAG}"
./scripts/deploy.sh "$ENV"
echo "Rolled back to ${TAG}. If a database migration must be undone, restore the pre-deployment backup (make restore)."
