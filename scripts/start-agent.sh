#!/data/data/com.termux/files/usr/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)"
APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
PID_FILE="$APP_HOME/run/agent.pid"
LOG_FILE="$APP_HOME/logs/agent.log"

mkdir -p "$APP_HOME/run" "$APP_HOME/logs"

if [ -s "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "DENG Tool: Rejoin is already running with PID $PID"
    exit 0
  fi
fi

cd "$PROJECT_DIR"
nohup python agent/deng_tool_rejoin.py --start >> "$LOG_FILE" 2>&1 &
echo "Started DENG Tool: Rejoin agent with PID $!"
