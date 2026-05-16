"""Bootstrap shell scripts for GET /install/* (Termux-friendly, LF-only).

Install is non-interactive: it downloads a small launcher tarball, extracts into
``~/.deng-tool/rejoin``, writes ``.install_requested`` and ``.install_api``, and
installs a wrapper that exports ``DENG_REJOIN_INSTALL_API``. License entry runs
only after ``deng-rejoin`` (:mod:`agent.deferred_bundle_install`).
"""

from __future__ import annotations

_INSTALL_PART_BEFORE_HEREDOC = r"""
command -v curl >/dev/null 2>&1 || { echo "Install curl first: pkg install -y curl" >&2; exit 1; }
command -v tar >/dev/null 2>&1 || { echo "Install tar first: pkg install -y tar" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Install python first: pkg install -y python" >&2; exit 1; }
LAUNCHER_URL="$DENG_REJOIN_INSTALL_API/install/launcher/bundle.tar.gz"
TMP="$(mktemp)"
STAGE="$(mktemp -d)"
trap 'rm -f "$TMP"; rm -rf "$STAGE"' EXIT
curl -fsSL "$LAUNCHER_URL" -o "$TMP" || { echo "Could not download launcher bundle." >&2; echo "URL: $LAUNCHER_URL" >&2; exit 1; }
tar -xzf "$TMP" -C "$STAGE" || { echo "Could not extract launcher bundle archive." >&2; exit 1; }
DEF_CK="$STAGE/agent/deferred_bundle_install.py"
if [[ ! -f "$DEF_CK" ]]; then
  echo "Failed launcher self-check." >&2
  echo "agent/deferred_bundle_install.py missing from launcher tarball." >&2
  echo "APP_HOME=$APP_HOME" >&2
  echo "Launcher bundle URL: $LAUNCHER_URL" >&2
  exit 1
fi
if ! grep -q "resolve_install_api" "$DEF_CK" 2>/dev/null; then
  echo "Failed launcher self-check." >&2
  echo "agent/deferred_bundle_install.py is missing resolve_install_api." >&2
  echo "The launcher bundle is stale or corrupted." >&2
  echo "APP_HOME=$APP_HOME" >&2
  echo "Launcher bundle URL: $LAUNCHER_URL" >&2
  echo "Deferred file path (staging): $DEF_CK" >&2
  exit 1
fi
mkdir -p "$APP_HOME/agent"
rm -f "$APP_HOME/agent/deng_tool_rejoin.py" "$APP_HOME/agent/deferred_bundle_install.py" "$APP_HOME/agent/__init__.py"
shopt -s nullglob
_LAUNCHER_CP=( "$STAGE"/agent/*.py )
if [[ ${#_LAUNCHER_CP[@]} -eq 0 ]]; then
  echo "Failed launcher self-check: no agent/*.py in bundle." >&2
  echo "Launcher bundle URL: $LAUNCHER_URL" >&2
  exit 1
fi
cp -a "${_LAUNCHER_CP[@]}" "$APP_HOME/agent/" || { echo "Failed to copy launcher Python files into APP_HOME." >&2; exit 1; }
shopt -u nullglob
if ! grep -q "resolve_install_api" "$APP_HOME/agent/deferred_bundle_install.py" 2>/dev/null; then
  echo "Failed launcher self-check: installed deferred_bundle_install.py still missing resolve_install_api." >&2
  echo "APP_HOME=$APP_HOME" >&2
  echo "Deferred path: $APP_HOME/agent/deferred_bundle_install.py" >&2
  exit 1
fi
echo "Launcher bundle verified."

# Prefer $PREFIX/bin (Termux: on PATH). Always mkdir — do not rely on [[ -w ]]
USING_HOME_BIN=0
BIN=""
if [[ -n "${PREFIX:-}" ]]; then
  if mkdir -p "${PREFIX}/bin" 2>/dev/null; then
    BIN="${PREFIX}/bin"
  fi
fi
if [[ -z "$BIN" ]]; then
  BIN="$HOME/bin"
  mkdir -p "$BIN"
  export PATH="$HOME/bin:$PATH"
  USING_HOME_BIN=1
  _MARK='# DENG Tool: Rejoin - PATH (added by installer)'
  _LINE='export PATH="$HOME/bin:$PATH"'
  for _rc in "$HOME/.bashrc" "$HOME/.profile"; do
    touch "$_rc"
    if ! grep -qF "$_MARK" "$_rc" 2>/dev/null; then
      printf '\n%s\n%s\n' "$_MARK" "$_LINE" >> "$_rc"
    fi
  done
fi

echo "Installing deng-rejoin wrapper to $BIN/deng-rejoin"
cat > "$BIN/deng-rejoin" << 'DENG_REJOIN_WRAPPER'
"""

_INSTALL_PART_AFTER_HEREDOC = r"""
DENG_REJOIN_WRAPPER
chmod +x "$BIN/deng-rejoin" || { echo "chmod failed: $BIN/deng-rejoin" >&2; exit 1; }
hash -r 2>/dev/null || true

_fail_install() {
  echo "Failed to create deng-rejoin command." >&2
  echo "Wrapper path: $BIN/deng-rejoin" >&2
  echo "PATH: $PATH" >&2
  echo "PREFIX: ${PREFIX:-<unset>}" >&2
  echo "Try: ls -la $BIN/deng-rejoin" >&2
  exit 1
}

[[ -s "$BIN/deng-rejoin" ]] || _fail_install
[[ -x "$BIN/deng-rejoin" ]] || _fail_install
[[ -f "$APP_HOME/.install_api" ]] && [[ -s "$APP_HOME/.install_api" ]] || _fail_install
[[ -f "$APP_HOME/agent/deng_tool_rejoin.py" ]] || _fail_install
[[ -f "$APP_HOME/agent/deferred_bundle_install.py" ]] || _fail_install

DR_RESOLVED=""
if command -v deng-rejoin >/dev/null 2>&1; then
  DR_RESOLVED="$(command -v deng-rejoin)"
fi
if [[ -z "$DR_RESOLVED" ]]; then
  echo "command -v deng-rejoin did not resolve after install." >&2
  _fail_install
fi

if ! PYTHONPATH="$APP_HOME" python3 -c "import agent.deferred_bundle_install" 2>/dev/null; then
  echo "Failed: launcher Python modules did not import. APP_HOME: $APP_HOME" >&2
  _fail_install
fi

set +e
_PY_ERR="$(PYTHONPATH="$APP_HOME" DENG_REJOIN_HOME="$APP_HOME" python3 -c "from agent.deferred_bundle_install import resolve_install_api" 2>&1)"
_PY_RC=$?
set -e
if [[ "$_PY_RC" -ne 0 ]]; then
  echo "Failed launcher self-check." >&2
  echo "agent/deferred_bundle_install.py is missing resolve_install_api or import failed." >&2
  echo "The launcher bundle may be stale or corrupted." >&2
  echo "APP_HOME=$APP_HOME" >&2
  echo "Launcher bundle URL: $LAUNCHER_URL" >&2
  echo "Expected file: $APP_HOME/agent/deferred_bundle_install.py" >&2
  echo "Python error:" >&2
  echo "$_PY_ERR" >&2
  exit 1
fi

# Prove first-run API resolves from ~/.install_api without install shell env
API_R="$(
  PYTHONPATH="$APP_HOME" DENG_REJOIN_HOME="$APP_HOME" \
  python3 -c 'import os; os.environ.pop("DENG_REJOIN_INSTALL_API", None); from agent.deferred_bundle_install import resolve_install_api; print(resolve_install_api())'
)" || API_R=""
if [[ -z "$API_R" ]]; then
  echo "Failed: could not resolve install API (resolve_install_api)." >&2
  _fail_install
fi
if [[ "$API_R" != "$DENG_REJOIN_INSTALL_API" ]]; then
  echo "Install API URL mismatch after resolve." >&2
  echo "Expected: $DENG_REJOIN_INSTALL_API" >&2
  echo "Got: $API_R" >&2
  exit 1
fi

echo "Wrapper path: $BIN/deng-rejoin"
echo "command -v deng-rejoin -> $DR_RESOLVED"
echo "resolve_install_api (env unset) -> $API_R"
if [[ "$USING_HOME_BIN" -eq 1 ]]; then
  echo 'Note: If deng-rejoin is not found in this shell, run once:'
  echo '  export PATH="$HOME/bin:$PATH"'
  echo '  hash -r'
fi
echo ""
echo "Install complete."
echo "Next: run deng-rejoin"
"""


def _sh_default_for_param_expansion(url: str) -> str:
    """Escape for use inside bash ``${{VAR:-{here}}}`` default segment."""
    return (url or "").replace("\\", "\\\\").replace("}", "\\}")


def wrapper_body_sh(install_api_base: str) -> str:
    """POSIX wrapper: set HOME, default API in env, python + entry checks, exec."""
    d = _sh_default_for_param_expansion(install_api_base.rstrip("/"))
    return (
        "#!/usr/bin/env sh\n"
        'export DENG_REJOIN_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"\n'
        f'export DENG_REJOIN_INSTALL_API="${{DENG_REJOIN_INSTALL_API:-{d}}}"\n'
        "if ! command -v python3 >/dev/null 2>&1; then\n"
        '  echo "deng-rejoin: python3 not found. Install: pkg install python" >&2\n'
        "  exit 127\n"
        "fi\n"
        'if [ ! -f "$DENG_REJOIN_HOME/agent/deng_tool_rejoin.py" ]; then\n'
        '  echo "deng-rejoin: missing $DENG_REJOIN_HOME/agent/deng_tool_rejoin.py" >&2\n'
        "  exit 1\n"
        "fi\n"
        'exec python3 "$DENG_REJOIN_HOME/agent/deng_tool_rejoin.py" "$@"\n'
    )


def render_public_bootstrap(
    *,
    base_url: str,
    requested: str,
    bootstrap_session: str = "",
    installer_title: str = "DENG Tool: Rejoin Installer",
    banner_lines: tuple[str, ...] = (),
    bundle_etag: str = "",
) -> str:
    """*requested* is ``latest``, ``v1.0.0``, ``main-dev``, ``test-latest``, etc."""
    base = base_url.rstrip("/")
    if bootstrap_session:
        safe_sess = _escape_double(bootstrap_session)
        sess_export = f'export BOOTSTRAP_SESSION="{safe_sess}"'
    else:
        sess_export = "unset BOOTSTRAP_SESSION 2>/dev/null || true"

    banner_part = ""
    if banner_lines:
        banner_part = "\n".join(f'echo "{_escape_double(line)}"' for line in banner_lines) + "\n"

    safe_title = _escape_double(installer_title)
    safe_req = _escape_double(requested)

    tail = (
        _INSTALL_PART_BEFORE_HEREDOC
        + wrapper_body_sh(base)
        + _INSTALL_PART_AFTER_HEREDOC
    )

    # Inject a cache-busting query parameter so CDN edges don't serve a stale bundle.
    if bundle_etag:
        _old = '"$DENG_REJOIN_INSTALL_API/install/launcher/bundle.tar.gz"'
        _new = f'"$DENG_REJOIN_INSTALL_API/install/launcher/bundle.tar.gz?v={bundle_etag}"'
        tail = tail.replace(_old, _new, 1)

    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'echo "{safe_title}"\n'
        f"{banner_part}"
        'if [[ -n "${PREFIX:-}" ]] && [[ "${PREFIX}" == *termux* ]]; then\n'
        '  echo "Detected: Termux"\n'
        "fi\n"
        f'export DENG_REJOIN_INSTALL_API="{base}"\n'
        f"{sess_export}\n"
        'APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"\n'
        'mkdir -p "$APP_HOME"\n'
        f'printf \'%s\\n\' "{safe_req}" > "$APP_HOME/.install_requested"\n'
        'printf \'%s\\n\' "$DENG_REJOIN_INSTALL_API" > "$APP_HOME/.install_api"\n'
        "if [[ -n \"${BOOTSTRAP_SESSION:-}\" ]]; then\n"
        '  printf \'%s\\n\' "$BOOTSTRAP_SESSION" > "$APP_HOME/.bootstrap_session"\n'
        "else\n"
        '  rm -f "$APP_HOME/.bootstrap_session"\n'
        "fi\n"
        f"{tail.lstrip()}"
    )


def _escape_double(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')
