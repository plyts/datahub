#!/usr/bin/env bash
# Create the demo users alice.admin / eric.engineer / anna.analyst / victor.viewer (dev/CI only).
set -euo pipefail
cd "$(dirname "$0")/.."
make seed-demo-users ENV="${1:-dev}"
