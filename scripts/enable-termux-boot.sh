#!/data/data/com.termux/files/usr/bin/sh
set -eu

APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
cd "$APP_HOME" || exit 1
exec python agent/deng_tool_rejoin.py enable-boot "$@"
