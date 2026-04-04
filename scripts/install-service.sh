#!/bin/bash
# Install DocFlow as a macOS LaunchAgent (runs on login, auto-restarts)
set -e

PLIST_SRC="$(dirname "$0")/../com.docflow.ui.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.docflow.ui.plist"
LOG_DIR="$(dirname "$0")/../logs"

# Create log directory
mkdir -p "$LOG_DIR"

# Copy plist
cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed: $PLIST_DST"

# Load the service
launchctl load "$PLIST_DST"
echo "Service loaded. DocFlow UI is now running at http://localhost:8765"
echo ""
echo "Useful commands:"
echo "  Stop:    launchctl unload ~/Library/LaunchAgents/com.docflow.ui.plist"
echo "  Start:   launchctl load ~/Library/LaunchAgents/com.docflow.ui.plist"
echo "  Logs:    tail -f $(cd "$(dirname "$0")/.." && pwd)/logs/docflow.log"
echo "  Errors:  tail -f $(cd "$(dirname "$0")/.." && pwd)/logs/docflow.err"
