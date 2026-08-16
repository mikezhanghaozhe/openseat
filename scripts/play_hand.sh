#!/usr/bin/env bash
# M1 gate (docs/MILESTONES.md): drives 4 seats to showdown through HTTP
# only. Starts its own server instance for the duration of the run (the
# same app `make dev` serves — packages.room_server.main:app now registers
# the real holdem-nl adapter by default, see docs/DECISIONS.md, "M1 gate
# verification pass" — this used to need its own wiring script, no longer
# does), then tears it down unconditionally.
#
# Exits non-zero on any unexpected status: a failure to start the server,
# a failure to reach it, or scripts/play_hand.py's own non-zero exit all
# propagate here.
set -u -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

PORT="${ARENA_PLAY_HAND_PORT:-8931}"
BASE_URL="http://127.0.0.1:${PORT}"

if [ -n "${PYTHON:-}" ]; then
    : # explicit override, e.g. for environments without `make install`'s venv
elif [ -x ".venv/bin/python3" ]; then
    PYTHON=".venv/bin/python3"
else
    PYTHON="python3"
fi

SERVER_LOG="$(mktemp -t play_hand_server.XXXXXX)"
SERVER_PID=""

cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null
        wait "$SERVER_PID" 2>/dev/null
    fi
    rm -f "$SERVER_LOG"
}
trap cleanup EXIT INT TERM

echo "starting room server on ${BASE_URL} ..."
"$PYTHON" -m uvicorn packages.room_server.main:app --host 127.0.0.1 --port "$PORT" --log-level warning >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

READY=""
for _ in $(seq 1 50); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "server process exited before becoming ready:" >&2
        cat "$SERVER_LOG" >&2
        exit 1
    fi
    if curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/v1/rooms/does-not-exist" 2>/dev/null | grep -q '^404$'; then
        READY=1
        break
    fi
    sleep 0.2
done

if [ -z "$READY" ]; then
    echo "server did not become ready within the timeout:" >&2
    cat "$SERVER_LOG" >&2
    exit 1
fi
echo "server ready"

ARENA_BASE_URL="$BASE_URL" "$PYTHON" "$REPO_ROOT/scripts/play_hand.py"
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
    echo "--- server log ---" >&2
    cat "$SERVER_LOG" >&2
fi

exit "$STATUS"
