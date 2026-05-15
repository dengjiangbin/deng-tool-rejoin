#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

OWNER="dengjiangbin"
REPO="deng-tool-rejoin"
BRANCH="main"
REMOTE="https://github.com/${OWNER}/${REPO}.git"
RAW_INSTALL_URL="https://raw.githubusercontent.com/${OWNER}/${REPO}/${BRANCH}/install.sh"
APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"

pink() {
  printf '\033[95m%s\033[0m\n' "$1"
}

title() {
  pink 'DDDDD   EEEEE  N   N   GGGG'
  pink 'D    D  E      NN  N  G'
  pink 'D    D  EEEE   N N N  G  GG'
  pink 'D    D  E      N  NN  G   G'
  pink 'DDDDD   EEEEE  N   N   GGG'
  echo 'Tool: Rejoin v1.0.0 installer'
  echo
}

die() {
  echo "Install failed: $*" >&2
  exit 1
}

is_termux() {
  [ -n "${PREFIX:-}" ] && echo "$PREFIX" | grep -q 'com.termux'
}

install_packages() {
  command -v pkg >/dev/null 2>&1 || die "Termux pkg command was not found."
  echo "Updating Termux packages..."
  pkg update -y
  pkg upgrade -y
  if ! pkg install -y python sqlite curl git android-tools; then
    echo "Warning: android-tools was not available from this Termux repository."
    pkg install -y python sqlite curl git
  fi
  if pkg show tsu >/dev/null 2>&1; then
    pkg install -y tsu || echo "Optional tsu install failed; continuing without it."
  else
    echo "Optional tsu package is not available; root mode can still use su if present."
  fi
}

prepare_storage() {
  if command -v termux-setup-storage >/dev/null 2>&1; then
    echo "Requesting Termux storage permission..."
    termux-setup-storage || echo "Storage permission was skipped or denied; continuing."
  fi
}

find_source() {
  local here
  here="$(pwd)"
  if [ -f "$here/agent/deng_tool_rejoin.py" ] && [ -f "$here/VERSION" ]; then
    echo "$here"
    return 0
  fi

  local script_dir
  script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)"
  if [ -n "$script_dir" ] && [ -f "$script_dir/agent/deng_tool_rejoin.py" ]; then
    echo "$script_dir"
    return 0
  fi

  local tmp
  tmp="$(mktemp -d "${TMPDIR:-/data/data/com.termux/files/usr/tmp}/deng-rejoin.XXXXXX")"
  echo "Cloning ${REMOTE}..." >&2
  git clone --depth 1 --branch "$BRANCH" "$REMOTE" "$tmp/repo"
  echo "$tmp/repo"
}

install_files() {
  local source_dir="$1"
  mkdir -p "$HOME/.deng-tool" "$APP_HOME" "$APP_HOME/data" "$APP_HOME/logs" "$APP_HOME/run" "$APP_HOME/launcher"

  for name in agent scripts docs examples tests VERSION README.md SECURITY.md INSTALL_TERMUX.md install.sh .gitignore; do
    if [ -e "$source_dir/$name" ]; then
      rm -rf "$APP_HOME/$name"
      cp -R "$source_dir/$name" "$APP_HOME/$name"
    fi
  done

  if [ -d "$source_dir/.git" ]; then
    rm -rf "$APP_HOME/.git"
    cp -R "$source_dir/.git" "$APP_HOME/.git"
  fi

  chmod +x "$APP_HOME/install.sh" 2>/dev/null || true
  chmod +x "$APP_HOME/scripts/"*.sh 2>/dev/null || true
}

write_wrapper() {
  local path="$1"
  local command="$2"
  cat > "$path" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
cd "\$HOME/.deng-tool/rejoin" || exit 1
exec python agent/deng_tool_rejoin.py ${command} "\$@"
EOF
  chmod +x "$path"
}

create_global_commands() {
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
}

write_python_launcher() {
  local path="$1"
  mkdir -p "$(dirname "$path")" 2>/dev/null || return 1
  cat > "$path" <<'EOF'
import os
import subprocess
import sys

home = os.path.expanduser("~")
project = os.path.join(home, ".deng-tool", "rejoin")
main = os.path.join(project, "agent", "deng_tool_rejoin.py")

if not os.path.exists(main):
    print("DENG Tool: Rejoin is not installed.")
    print("Run the GitHub install command first.")
    sys.exit(1)

os.chdir(project)
raise SystemExit(subprocess.call(["python", main, "menu"] + sys.argv[1:]))
EOF
}

create_market_launchers() {
  write_python_launcher "$APP_HOME/launcher/deng-rejoin.py" || true
  for dir in /sdcard/Download /sdcard/download /storage/emulated/0/Download /storage/emulated/0/download; do
    if [ -d "$dir" ]; then
      write_python_launcher "$dir/deng-rejoin.py" && echo "Launcher created: $dir/deng-rejoin.py" || true
    fi
  done
}

main() {
  title
  is_termux || echo "Warning: this installer is designed for Termux on Android."
  prepare_storage
  install_packages
  source_dir="$(find_source)"
  install_files "$source_dir"
  create_global_commands
  create_market_launchers
  echo
  echo "Running doctor..."
  python "$APP_HOME/agent/deng_tool_rejoin.py" --doctor || true
  echo
  echo "Install complete."
  echo
  echo "Next steps:"
  echo "  1. Start the tool: deng-rejoin"
  echo "  2. Enter your license key when prompted."
  echo "  3. Run First Time Setup Config from the menu."
  echo "  4. Select your detected Roblox package (or enter it manually)."
  echo "  5. Add a private server URL in setup if you need direct join."
  echo "  6. Choose Start when you are ready."
  echo
  echo "Full beginner guide:"
  echo "  $APP_HOME/docs/NEW_USER_TERMUX_GUIDE.md"
  echo
  echo "Other commands:"
  echo "  deng-rejoin-setup   (setup wizard / config entry)"
  echo "  deng-rejoin-start   (Start supervisor from CLI)"
  echo "  deng-rejoin-update  (update from GitHub when installed via git)"
  echo "  deng-rejoin-status"
  echo "  deng-rejoin doctor"
  echo
  echo "Test one rejoin:"
  echo "python $APP_HOME/agent/deng_tool_rejoin.py --once"
  echo
  echo "Optional Download-folder launcher (if created):"
  echo "python /sdcard/Download/deng-rejoin.py"
}

main "$@"
