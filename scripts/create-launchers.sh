#!/data/data/com.termux/files/usr/bin/sh
set -eu

APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
mkdir -p "$APP_HOME/launcher"
cd "$APP_HOME" || exit 1

if [ -z "${PREFIX:-}" ]; then
  echo "PREFIX is not set. Run this from Termux."
  exit 1
fi

write_wrapper() {
  path="$1"
  command="$2"
  cat > "$path" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
cd "\$HOME/.deng-tool/rejoin" || exit 1
exec python agent/deng_tool_rejoin.py ${command} "\$@"
EOF
  chmod +x "$path"
}

mkdir -p "$PREFIX/bin"
cat > "$PREFIX/bin/deng-rejoin" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
cd "$HOME/.deng-tool/rejoin" || exit 1
if [ "$#" -gt 0 ]; then
  exec python agent/deng_tool_rejoin.py "$@"
fi
exec python agent/deng_tool_rejoin.py menu
EOF
chmod +x "$PREFIX/bin/deng-rejoin"

write_wrapper "$PREFIX/bin/deng-rejoin-setup" "setup"
write_wrapper "$PREFIX/bin/deng-rejoin-start" "start"
write_wrapper "$PREFIX/bin/deng-rejoin-stop" "stop"
write_wrapper "$PREFIX/bin/deng-rejoin-status" "status"
write_wrapper "$PREFIX/bin/deng-rejoin-logs" "logs"
write_wrapper "$PREFIX/bin/deng-rejoin-update" "update"
write_wrapper "$PREFIX/bin/deng-rejoin-reset" "reset"

python agent/deng_tool_rejoin.py --version >/dev/null
python - <<'PY'
from agent.launcher_file import create_market_launchers
for path in create_market_launchers():
    print(f"Launcher created: {path}")
PY
