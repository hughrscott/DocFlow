#!/bin/bash
# Wrapper script for launchd — captures all output including pre-Python errors
DEBUG_LOG="/Users/hughscott/Documents/Coding/DocFlow/logs/docflow-debug.log"
echo "=== $(date) ===" >> "$DEBUG_LOG"
echo "PID: $$" >> "$DEBUG_LOG"
echo "arch: $(uname -m)" >> "$DEBUG_LOG"
echo "PATH: $PATH" >> "$DEBUG_LOG"
echo "which python: $(which python 2>&1)" >> "$DEBUG_LOG"
echo "python arch: $(file /Users/hughscott/Documents/Coding/DocFlow/.venv/bin/python3.13 2>&1)" >> "$DEBUG_LOG"

exec /usr/bin/arch -arm64 /Users/hughscott/Documents/Coding/DocFlow/.venv/bin/python \
    /Users/hughscott/Documents/Coding/DocFlow/main.py \
    --config /Users/hughscott/Documents/Coding/DocFlow/config/user_config.yaml \
    ui --port 8765 >> "$DEBUG_LOG" 2>&1
