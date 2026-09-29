#!/bin/bash
# Stops and removes the agent-iV LaunchAgents installed by install.sh.
set -euo pipefail

PLIST_DIR="$HOME/Library/LaunchAgents"

for name in backend frontend; do
    dest="$PLIST_DIR/com.agentiv.$name.plist"
    if [ -f "$dest" ]; then
        launchctl unload "$dest" 2>/dev/null || true
        rm "$dest"
        echo "Removed com.agentiv.$name"
    else
        echo "com.agentiv.$name not installed, skipping"
    fi
done
