#!/bin/bash
# Uninstall DocFlow LaunchAgent
set -e

PLIST_DST="$HOME/Library/LaunchAgents/com.docflow.ui.plist"

if [ -f "$PLIST_DST" ]; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm "$PLIST_DST"
    echo "DocFlow service uninstalled."
else
    echo "Service not installed."
fi
