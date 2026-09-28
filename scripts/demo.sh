#!/bin/sh
# Run the whole demo: optionally start from a fresh database, then walk one
# event from set-up to signed results, narrating each step.
#
#   sh scripts/demo.sh            # against the running stack
#   sh scripts/demo.sh --fresh    # docker compose down -v && up first
#   sh scripts/demo.sh --pause    # wait for Enter before each step (for recording)
#
# Needs Docker (for --fresh) and Python 3 (standard library only).
set -eu
cd "$(dirname "$0")/.."
BASE=${BALLOTBENCH_URL:-http://localhost:8080}
PASS=""
for arg in "$@"; do
  case "$arg" in
    --fresh)
      echo "Starting from an empty database..."
      docker compose down -v >/dev/null 2>&1 || true
      docker compose up -d --build --wait --wait-timeout 300 >/dev/null
      ;;
    --pause) PASS="$PASS --pause" ;;
    *) PASS="$PASS $arg" ;;
  esac
done
# shellcheck disable=SC2086
python3 scripts/demo.py --base "$BASE" $PASS
