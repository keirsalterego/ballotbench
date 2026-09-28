#!/bin/sh
# Run the whole demo from a fresh clone: build and start the portal if it
# isn't running, then walk one event from set-up to signed results,
# narrating each step.
#
#   sh scripts/demo.sh             # start the portal if needed, then run
#   sh scripts/demo.sh --fresh     # throw away the database first, then run
#   sh scripts/demo.sh --pause     # wait for Enter before each step (for recording)
#
#   BALLOTBENCH_PORT=9000 sh scripts/demo.sh    # if 8080 is taken
#
# Needs Docker with the Compose plugin, and Python 3 (standard library only).
set -eu
cd "$(dirname "$0")/.."

PORT=${BALLOTBENCH_PORT:-8080}
export BALLOTBENCH_PORT="$PORT"
BASE=${BALLOTBENCH_URL:-http://localhost:$PORT}
LOG=${TMPDIR:-/tmp}/ballotbench-demo-compose.log
FRESH=0
PASS=""
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=1 ;;
    *) PASS="$PASS $arg" ;;
  esac
done

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "The demo needs $1, and it isn't installed." >&2; exit 1; }
}

answering() {
  python3 -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1] + '/projects', timeout=3)" \
    "$BASE" >/dev/null 2>&1
}

# Compose's live progress display needs a console of its own, and fails with
# "failed to get console" when its output is redirected. With all of its
# output going to a file, it writes plain lines instead.
compose() {
  docker compose "$@" >>"$LOG" 2>&1 </dev/null
}

start() {
  need docker
  echo "Building and starting the portal on port $PORT (the first build takes a few minutes)..."
  if ! compose up -d --build --wait --wait-timeout 600; then
    echo "docker compose up failed. The last lines of $LOG:" >&2
    tail -20 "$LOG" >&2
    exit 1
  fi
}

need python3
: >"$LOG"
if [ "$FRESH" = 1 ]; then
  need docker
  echo "Throwing away the old database..."
  compose down -v || true
  start
elif ! answering; then
  echo "Nothing is answering at $BASE yet."
  start
fi
if ! answering; then
  echo "The portal isn't answering at $BASE. Check: docker compose ps, and $LOG" >&2
  exit 1
fi
echo "Portal is up at $BASE."
# shellcheck disable=SC2086
exec python3 scripts/demo.py --base "$BASE" $PASS
