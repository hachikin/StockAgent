#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/log/bot_server.pid"
SERVER_LOG="$ROOT_DIR/log/server.log"
PYTHON_BIN="/root/venv/bin/python"

mkdir -p "$ROOT_DIR/log"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "bot.server already running, pid=$OLD_PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

cd "$ROOT_DIR"
nohup "$PYTHON_BIN" -m bot.server >>"$SERVER_LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

echo "bot.server started, pid=$NEW_PID"
echo "pid file: $PID_FILE"
echo "log file: $SERVER_LOG"
