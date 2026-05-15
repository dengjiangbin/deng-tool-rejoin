"""Bootstrap shell scripts for GET /install/* (Termux-friendly, LF-only).

Install is non-interactive: it downloads a small launcher tarball, extracts into
``~/.deng-tool/rejoin``, and writes ``.install_requested``. License-gated download
of the full bundle runs on first ``deng-rejoin`` (:mod:`agent.deferred_bundle_install`).

On Termux, ``deng-rejoin`` is installed under ``$PREFIX/bin`` so it is on PATH
immediately (no restart). Fallback: ``$HOME/bin`` with PATH persisted in shell rc files.
"""

from __future__ import annotations

# shell: POSIX ``sh`` — Termux provides ``/usr/bin/env``; checks before exec.
_WRAPPER_BODY = """#!/usr/bin/env sh
export DENG_REJOIN_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
if ! command -v python3 >/dev/null 2>&1; then
  echo "deng-rejoin: python3 not found. Install: pkg install python" >&2
  exit 127
fi
if [ ! -f "$DENG_REJOIN_HOME/agent/deng_tool_rejoin.py" ]; then
  echo "deng-rejoin: missing $DENG_REJOIN_HOME/agent/deng_tool_rejoin.py (re-run install)" >&2
  exit 1
fi
exec python3 "$DENG_REJOIN_HOME/agent/deng_tool_rejoin.py" "$@"
"""

# Installing to $HOME/.local/bin breaks Termux (not on default PATH).
_INSTALL_PART_BEFORE_HEREDOC = r"""
command -v curl >/dev/null 2>&1 || { echo "Install curl first: pkg install -y curl" >&2; exit 1; }
command -v tar >/dev/null 2>&1 || { echo "Install tar first: pkg install -y tar" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Install python first: pkg install -y python" >&2; exit 1; }
LAUNCHER_URL="$DENG_REJOIN_INSTALL_API/install/launcher/bundle.tar.gz"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$LAUNCHER_URL" -o "$TMP" || { echo "Could not download launcher bundle." >&2; exit 1; }
tar -xzf "$TMP" -C "$APP_HOME"

INSTALL_BIN=""
if [[ -n "${PREFIX:-}" ]] && [[ -d "${PREFIX}/bin" ]] && [[ -w "${PREFIX}/bin" ]]; then
  INSTALL_BIN="${PREFIX}/bin"
elif [[ -n "${PREFIX:-}" ]]; then
  if mkdir -p "${PREFIX}/bin" 2>/dev/null && [[ -w "${PREFIX}/bin" ]]; then
    INSTALL_BIN="${PREFIX}/bin"
  fi
fi

USING_HOME_BIN=0
if [[ -n "$INSTALL_BIN" ]]; then
  BIN="$INSTALL_BIN"
else
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

cat > "$BIN/deng-rejoin" << 'DENG_REJOIN_WRAPPER'
"""

_INSTALL_PART_AFTER_HEREDOC = r"""
DENG_REJOIN_WRAPPER
chmod +x "$BIN/deng-rejoin"

_fail_install() {
  echo "Failed to create deng-rejoin command." >&2
  echo "Wrapper path: $BIN/deng-rejoin" >&2
  echo "PATH: $PATH" >&2
  echo "Try direct path: $BIN/deng-rejoin" >&2
  exit 1
}

[[ -f "$BIN/deng-rejoin" ]] || _fail_install
[[ -x "$BIN/deng-rejoin" ]] || _fail_install
[[ -f "$APP_HOME/agent/deng_tool_rejoin.py" ]] || _fail_install
[[ -f "$APP_HOME/agent/deferred_bundle_install.py" ]] || _fail_install

DR_RESOLVED="$(command -v deng-rejoin 2>/dev/null || true)"
if [[ -z "$DR_RESOLVED" ]]; then
  _fail_install
fi

if ! PYTHONPATH="$APP_HOME" python3 -c "import agent.deferred_bundle_install" 2>/dev/null; then
  echo "Failed: launcher Python modules did not import. Check PYTHONPATH / extract path." >&2
  echo "APP_HOME: $APP_HOME" >&2
  _fail_install
fi

echo "deng-rejoin command: $DR_RESOLVED"
if [[ "$USING_HOME_BIN" -eq 1 ]]; then
  echo 'Note: If deng-rejoin is not found in this shell, run once:'
  echo '  export PATH="$HOME/bin:$PATH"'
fi
echo ""
echo "Install complete."
echo "Next: run deng-rejoin"
"""

_INSTALL_TAIL = _INSTALL_PART_BEFORE_HEREDOC + _WRAPPER_BODY + _INSTALL_PART_AFTER_HEREDOC


def render_public_bootstrap(
    *,
    base_url: str,
    requested: str,
    bootstrap_session: str = "",
    installer_title: str = "DENG Tool: Rejoin Installer",
    banner_lines: tuple[str, ...] = (),
) -> str:
    """*requested* is ``latest``, ``v1.0.0``, ``main-dev``, ``test-latest``, etc.

    Optional *bootstrap_session* for legacy signed ``/install/dev/main`` installs;
    written to ``.bootstrap_session`` for the first ``deng-rejoin`` run.
    """
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
        "if [[ -n \"${BOOTSTRAP_SESSION:-}\" ]]; then\n"
        '  printf \'%s\\n\' "$BOOTSTRAP_SESSION" > "$APP_HOME/.bootstrap_session"\n'
        "else\n"
        '  rm -f "$APP_HOME/.bootstrap_session"\n'
        "fi\n"
        f"{_INSTALL_TAIL.lstrip()}"
    )


def _escape_double(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')
