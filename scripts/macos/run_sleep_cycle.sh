#!/bin/bash
# Triggers the nightly reflection cycle, then turns whatever it (or a
# human, via /approvals/pending) has approved into real tasks. Meant to
# run on a schedule via com.agentiv.sleepcycle.plist.template — see
# docs/DEVELOPMENT_SETUP.md for how to install it.
#
# Materializing the backlog right after the cycle is safe to automate:
# every item it touches was already approved by a human at some point
# before this ran (either just now via a same-day approval, or on a
# previous day) — this script never decides anything, it only acts on
# decisions that already happened. See docs/HAVEN.md.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# launchd doesn't run a login shell, so backend/.env isn't sourced
# automatically the way an interactive terminal session would have it —
# load it directly if present, without overriding anything already set
# in the environment this script was actually invoked with.
if [ -f "$REPO_DIR/backend/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_DIR/backend/.env"
    set +a
fi

BASE_URL="${AGENT_IV_BASE_URL:-http://localhost:8024}"  # core/configuration/ports.py

if [ -z "${INTERNAL_TRIGGER_SECRET:-}" ]; then
    echo "INTERNAL_TRIGGER_SECRET is not set (checked the environment and backend/.env)." >&2
    exit 1
fi

echo "==> Triggering Sleep Cycle at $BASE_URL"
curl -sf -X POST "$BASE_URL/internal/sleep-cycle" -H "x-internal-secret: $INTERNAL_TRIGGER_SECRET"
echo

echo "==> Materializing approved backlog"
curl -sf -X POST "$BASE_URL/internal/materialize-backlog" -H "x-internal-secret: $INTERNAL_TRIGGER_SECRET"
echo
