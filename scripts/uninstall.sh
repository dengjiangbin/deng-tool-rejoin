#!/data/data/com.termux/files/usr/bin/sh
set -eu

APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"

echo "This will remove DENG Tool: Rejoin command wrappers."
echo "App data at $APP_HOME is kept unless you confirm removal."
python "$APP_HOME/agent/deng_tool_rejoin.py" stop 2>/dev/null || true

if [ -n "${PREFIX:-}" ]; then
  for command in deng-rejoin deng-rejoin-setup deng-rejoin-start deng-rejoin-stop deng-rejoin-status deng-rejoin-logs deng-rejoin-update deng-rejoin-reset; do
    rm -f "$PREFIX/bin/$command"
  done
fi

printf "Remove app files and local data at %s? [y/N]: " "$APP_HOME"
read answer
case "$answer" in
  y|Y|yes|YES)
    rm -rf "$APP_HOME"
    echo "DENG Tool: Rejoin app files removed."
    ;;
  *)
    echo "Command wrappers removed. App files kept."
    ;;
esac
