#!/bin/bash
# One command to bring iV up locally: ./run.sh
#
# Starts the API server and the chat frontend, waits for the API's health
# endpoint to actually report ready (rather than assuming a started
# process is a working one), prints every URL the service is reachable on
# — including the Tailscale one, discovered at runtime, never hardcoded —
# and shuts both down cleanly on Ctrl+C.
#
# For a service that survives logout and reboot, install the LaunchAgents
# instead: scripts/macos/install.sh (see docs/RUNTIME.md).
#
# Flags:
#   --dev         enable uvicorn's auto-reloader (edit-and-refresh)
#   --api-only    skip the frontend
set -uo pipefail

# Job control, so each background job below becomes its own process
# group. Without it, `kill $pid` on the frontend job kills only the
# subshell that launched npm, leaving `next dev` orphaned and still
# holding port 3000 — verified by watching exactly that happen.
set -m

cd "$(dirname "${BASH_SOURCE[0]}")"

DEV_MODE=0
API_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --dev) DEV_MODE=1 ;;
        --api-only) API_ONLY=1 ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

# Keep in sync with core/configuration/ports.py — shell cannot import it,
# and tests/core/test_ports.py asserts these have not drifted.
IV_PORT="${IV_PORT:-8024}"
FRONTEND_PORT="${FRONTEND_PORT:-4024}"
BACKEND_PID=""
FRONTEND_PID=""

# Both children get SIGTERM and a chance to run their own shutdown (the
# API closes its SQLite handle in the FastAPI lifespan) before this script
# exits. The previous version killed only the backend and left the Next.js
# dev server orphaned, holding port 3000 until it was found by hand.
# Signals the whole process group (the leading '-' on the pid) so npm's
# own child processes go down with it, falling back to the bare pid if the
# group is already gone. Each child still receives SIGTERM and runs its
# own shutdown — the API closes its SQLite handle in the FastAPI lifespan.
stop_tree() {
    local pid="$1"
    [ -n "$pid" ] || return 0
    kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
}

shutdown() {
    trap - INT TERM EXIT
    echo
    echo "===> Stopping iV..."
    stop_tree "$FRONTEND_PID"
    stop_tree "$BACKEND_PID"
    for pid in "$FRONTEND_PID" "$BACKEND_PID"; do
        [ -n "$pid" ] && wait "$pid" 2>/dev/null
    done
    echo "===> iV stopped."
}
trap shutdown INT TERM EXIT

echo "===> iV — local runtime"
echo "===> Branch: $(git branch --show-current 2>/dev/null || echo 'not a git checkout')"

# iV's source uses PEP 604 unions (`str | None`) in 48 modules, so 3.10 is
# a hard floor, not a preference. Checking here turns "TypeError:
# unsupported operand type(s) for |" raised somewhere deep in an import
# into a sentence that says what to install. macOS 11 and 12 ship 3.8/3.9
# as `python3`, so this is the first thing that bites on an older Mac.
PYTHON_BIN="${PYTHON_BIN:-python3}"
MIN_PY_TEXT="3.10"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "===> '$PYTHON_BIN' not found on PATH." >&2
    echo "     Install Python ${MIN_PY_TEXT}+ from https://www.python.org/downloads/macos/" >&2
    exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    FOUND_VERSION="$("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo unknown)"
    cat >&2 <<EOF

===> iV needs Python ${MIN_PY_TEXT} or newer. '$PYTHON_BIN' is ${FOUND_VERSION}.

     iV's source uses the \`str | None\` type syntax introduced in 3.10.
     On macOS 11/12 the system python3 is too old for this.

     Options:
       1. Install from https://www.python.org/downloads/macos/ (works on
          macOS 11+), then re-run this script.
       2. If you already have a newer interpreter, point at it directly:
            PYTHON_BIN=/usr/local/bin/python3.11 ./run.sh
       3. pyenv: pyenv install 3.11 && pyenv local 3.11

     Already-installed interpreters found on this machine:
EOF
    ls /usr/local/bin/python3.1* /opt/homebrew/bin/python3.1* \
       /Library/Frameworks/Python.framework/Versions/3.1*/bin/python3 2>/dev/null \
       | sed 's/^/       /' >&2 || echo "       (none besides $PYTHON_BIN)" >&2
    echo >&2
    exit 1
fi

if [ ! -d "backend/venv" ]; then
    echo "===> Creating Python virtual environment ($("$PYTHON_BIN" -V 2>&1))..."
    "$PYTHON_BIN" -m venv backend/venv || exit 1
elif ! backend/venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    # An existing venv built by an older interpreter stays old forever;
    # upgrading the system python does not migrate it.
    echo "===> backend/venv was built with Python $(backend/venv/bin/python -V 2>&1 | cut -d" " -f2)," >&2
    echo "     which is too old. Remove it and re-run:  rm -rf backend/venv" >&2
    exit 1
fi
source backend/venv/bin/activate
# Always sync, not just on first creation — an existing venv that predates
# a requirements.txt change (e.g. a newly added provider SDK) would
# otherwise silently miss it and crash on import at startup instead of
# failing here with a clear message.
pip install -q -r backend/requirements.txt || exit 1

if [ "$DEV_MODE" = "1" ]; then
    export IV_DEV=1
    echo "===> Development mode: auto-reload enabled"
fi

# Refuse to start on a port something else already owns. Without this the
# health gate below is answered by whatever is already listening -- an iV
# left running from yesterday, say -- so this script printed "API healthy"
# and "iV is up" while its own child died on EADDRINUSE, and the failure
# scrolled past above a reassuring banner. Verified in the wild: a /health
# reporting 52685 seconds of uptime, three seconds after "starting".
port_owner() {
    lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null | head -1
}

for occupied_port in "$IV_PORT" "$FRONTEND_PORT"; do
    [ "$occupied_port" = "$FRONTEND_PORT" ] && [ "$API_ONLY" = "1" ] && continue
    owner_pid="$(port_owner "$occupied_port")"
    if [ -n "$owner_pid" ]; then
        echo "===> Port ${occupied_port} is already in use by pid ${owner_pid}:" >&2
        ps -p "$owner_pid" -o pid=,command= 2>/dev/null | sed 's/^/       /' >&2
        echo >&2
        echo "     iV is probably already running. Either use the running one, or:" >&2
        echo "       scripts/ivctl restart     # if you installed the LaunchAgents" >&2
        echo "       kill ${owner_pid}         # if it is a stray from an earlier run" >&2
        echo >&2
        echo "     Refusing to start a second copy -- the health check below would" >&2
        echo "     be answered by the old one and this would look like it worked." >&2
        exit 1
    fi
done

echo "===> Starting iV API on port ${IV_PORT}..."
python run.py &
BACKEND_PID=$!

echo -n "===> Waiting for /health"
READY=0
for _ in $(seq 1 60); do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo
        echo "===> API exited during startup. See the output above." >&2
        exit 1
    fi
    if curl -fsS "http://127.0.0.1:${IV_PORT}/health" >/dev/null 2>&1; then
        READY=1
        break
    fi
    echo -n "."
    sleep 1
done
echo
if [ "$READY" != "1" ]; then
    echo "===> API did not become healthy within 60s." >&2
    exit 1
fi

HEALTH=$(curl -fsS "http://127.0.0.1:${IV_PORT}/health" 2>/dev/null)

# Belt as well as braces: confirm the process answering /health is the one
# this script started. The pre-flight check above closes the common case,
# but a race (something grabbing the port in the second between the two)
# would otherwise still be reported as a successful start.
HEALTH_OWNER="$(port_owner "$IV_PORT")"
if [ -n "$HEALTH_OWNER" ] && [ "$HEALTH_OWNER" != "$BACKEND_PID" ]; then
    echo >&2
    echo "===> Port ${IV_PORT} is being served by pid ${HEALTH_OWNER}, not the API this" >&2
    echo "     script just started (pid ${BACKEND_PID}). Not reporting a successful start." >&2
    exit 1
fi

echo "===> API healthy: $HEALTH"

if [ "$API_ONLY" != "1" ]; then
    if [ ! -d "frontend/node_modules" ]; then
        echo "===> Installing frontend dependencies..."
        (cd frontend && npm install) || exit 1
    fi
    echo "===> Starting chat frontend on port ${FRONTEND_PORT}..."
    # FRONTEND_PORT is exported rather than passed as --port: package.json's
    # dev script already reads it, and passing both put "--port X --port X"
    # on the resulting next command line.
    (cd frontend && IV_API_URL="http://127.0.0.1:${IV_PORT}" FRONTEND_PORT="${FRONTEND_PORT}" npm run dev) &
    FRONTEND_PID=$!
fi

echo
echo "===================== iV is up ====================="
python - "$IV_PORT" "$FRONTEND_PORT" "$API_ONLY" <<'PYEOF'
import sys
from core.environment.network import discover_access

api_port, frontend_port, api_only = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3] == "1"
api = discover_access(api_port)

# Under --api-only there is no frontend to advertise. Printing its URL
# anyway sends the reader to a dead port and makes them debug the wrong
# process.
if not api_only:
    frontend = discover_access(frontend_port)
    print(f"  Chat frontend : {'  '.join(frontend.local_urls)}")

print(f"  API           : {'  '.join(api.local_urls)}")
print(f"  Health        : {api.local_urls[0]}/health")

reachable = api if api_only else frontend
label = "API over Tailscale" if api_only else "From iPhone"
if reachable.tailscale_urls:
    print(f"  {label:<14}: {reachable.tailscale_urls[-1]}")
else:
    print(f"  {label:<14}: unavailable — {reachable.note}")
PYEOF
echo "===================================================="
echo "Ctrl+C to stop."
echo

wait "$BACKEND_PID"
