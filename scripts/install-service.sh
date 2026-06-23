#!/bin/bash
# Install DocFlow as a macOS LaunchAgent (runs on login, auto-restarts)
set -e

PLIST_SRC="$(dirname "$0")/../com.docflow.ui.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.docflow.ui.plist"
LOG_DIR="$(dirname "$0")/../logs"
PORT=8765
UID_VAL=$(id -u)

# Create log directory
mkdir -p "$LOG_DIR"

# Fully remove existing service (clears throttle state)
echo "Removing any existing DocFlow service..."
launchctl bootout "gui/$UID_VAL/com.docflow.ui" 2>/dev/null || true
launchctl unload "$PLIST_DST" 2>/dev/null || true
sleep 2

# Kill anything on the target port
if lsof -ti :"$PORT" &>/dev/null; then
    echo "Killing existing process on port $PORT..."
    lsof -ti :"$PORT" | xargs kill 2>/dev/null || true
    sleep 1
fi

# Copy plist
cp "$PLIST_SRC" "$PLIST_DST"
echo "Installed: $PLIST_DST"

# Load the service using bootstrap (modern API, resets throttle)
launchctl bootstrap "gui/$UID_VAL" "$PLIST_DST"
# Force immediate start
launchctl kickstart -k -p "gui/$UID_VAL/com.docflow.ui"

# Verify it actually started
sleep 3
if lsof -ti :"$PORT" &>/dev/null; then
    echo "DocFlow UI is running at http://localhost:$PORT"
else
    echo "WARNING: Service loaded but not listening on port $PORT."
    echo "Check logs for errors:"
    echo "  Errors:  tail -f $(cd "$(dirname "$0")/.." && pwd)/logs/docflow.err"
    echo "  Logs:    tail -f $(cd "$(dirname "$0")/.." && pwd)/logs/docflow.log"
    exit 1
fi

echo ""
echo "Useful commands:"
echo "  Stop:    launchctl bootout gui/$UID_VAL/com.docflow.ui"
echo "  Start:   launchctl bootstrap gui/$UID_VAL ~/Library/LaunchAgents/com.docflow.ui.plist"
echo "  Logs:    tail -f $(cd "$(dirname "$0")/.." && pwd)/logs/docflow.log"
echo "  Errors:  tail -f $(cd "$(dirname "$0")/.." && pwd)/logs/docflow.err"
