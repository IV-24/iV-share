#!/bin/bash
# Installs agent-iV as two macOS LaunchAgents (backend + frontend) so both
# start automatically at login and restart themselves if they crash.
#
# LaunchAgents run per-user, AFTER login — not before it. If this Mac
# doesn't already log in automatically, "starts when the Mac starts up"
# means "starts once you've logged in," not before. Enable automatic
# login (System Settings -> Users & Groups -> Login Options) if you want
# it available immediately after a reboot with nobody at the keyboard.
#
# Also worth checking: System Settings -> Battery/Energy -> "Prevent
# automatic sleeping when the display is off," otherwise a lid-closed or
# idle Mac can drop off Tailscale even though the LaunchAgents are fine.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$REPO_DIR/.launchd"

if [ ! -x "$REPO_DIR/backend/venv/bin/python" ]; then
    echo "backend/venv not found. Run ./run.sh once manually first (it" >&2
    echo "creates the venv and installs requirements), then re-run this." >&2
    exit 1
fi

if [ ! -d "$REPO_DIR/frontend/node_modules" ]; then
    echo "frontend/node_modules not found. Run ./run.sh once manually" >&2
    echo "first (or 'npm install' in frontend/), then re-run this." >&2
    exit 1
fi

# npm's absolute path is resolved HERE, while we are running in the
# user's normal interactive shell, and baked into the plist. launchd does
# not run a login shell, and a non-interactive login shell would not
# source .zshrc anyway — which is where nvm/volta put node on PATH. Doing
# the lookup at install time is what makes the frontend agent start
# reliably regardless of how node was installed.
NPM_BIN="$(command -v npm || true)"
if [ -z "$NPM_BIN" ]; then
    echo "npm not found on PATH. Install Node (20+) and re-run this script." >&2
    exit 1
fi
NODE_DIR="$(dirname "$NPM_BIN")"
echo "Using npm at: $NPM_BIN"

mkdir -p "$PLIST_DIR" "$LOG_DIR"

for name in backend frontend; do
    src="$REPO_DIR/scripts/macos/com.agentiv.$name.plist.template"
    dest="$PLIST_DIR/com.agentiv.$name.plist"
    sed -e "s#__REPO_DIR__#$REPO_DIR#g" \
        -e "s#__NPM_BIN__#$NPM_BIN#g" \
        -e "s#__NODE_DIR__#$NODE_DIR#g" "$src" > "$dest"

    launchctl unload "$dest" 2>/dev/null || true
    launchctl load -w "$dest"
    echo "Installed and loaded com.agentiv.$name -> $dest"
done

echo
echo "Status:  launchctl list | grep agentiv"
echo "Logs:    $LOG_DIR/"
echo "Stop:    scripts/macos/uninstall.sh"
