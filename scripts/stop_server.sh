#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/log/bot_server.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "pid file not found: $PID_FILE"
  exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "${PID:-}" ]]; then
  echo "empty pid file, removing"
  rm -f "$PID_FILE"
  exit 0
fi

if kill -0 "$PID" 2>/dev/null; then
  kill "$PID" || true
  for _ in {1..20}; do
    if kill -0 "$PID" 2>/dev/null; then
      sleep 0.2
    else
      break
    fi
  done

  if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID" || true
  fi

  echo "bot.server stopped, pid=$PID"
else
  echo "process not running, pid=$PID"
fi

rm -f "$PID_FILE"
