"""Bootstrap shell scripts for GET /install/* (Termux-friendly, LF-only).

Two install modes:

1. **Direct install** (preferred, used for test/latest and public stable releases):
   :func:`render_direct_install_bootstrap` downloads the *full* package tarball
   immediately.  No license key is required during install.  The license is only
   prompted inside the real tool on first run (inside the menu flow).

2. **Launcher-deferred install** (legacy, kept for reference):
   :func:`render_public_bootstrap` downloads a small launcher tarball and writes
   ``.install_requested``.  License entry runs only after ``deng-rejoin``
   (:mod:`agent.deferred_bundle_install`).
"""

from __future__ import annotations

# NOTE: The installer scripts are POSIX-sh only.  No bash features.
#
# Termux's ``/data/data/com.termux/files/usr/bin/sh`` is **dash**.
# When the user invokes ``curl ... | sh``, the shebang on line 1 is
# ignored and dash executes the bytes — it would choke on
# ``set -o pipefail``, ``[[ ... ]]``, ``shopt``, arrays, or
# ``${VAR:0:N}`` substring expansion.  We previously tried a
# "re-exec into bash" preamble, but ``exec bash "$0"`` failed on
# Termux because ``$0`` was the dash *binary* itself (not a script
# file).  The robust answer is to use POSIX-sh syntax throughout.

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
if [ ! -f "$DEF_CK" ]; then
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
# POSIX-sh equivalent of bash's ``shopt -s nullglob`` + array copy:
# iterate the glob, skip the literal-unmatched case explicitly.
_COPIED=0
for _f in "$STAGE"/agent/*.py; do
  [ -e "$_f" ] || continue
  cp -a "$_f" "$APP_HOME/agent/" || { echo "Failed to copy $_f into APP_HOME." >&2; exit 1; }
  _COPIED=1
done
if [ "$_COPIED" -ne 1 ]; then
  echo "Failed launcher self-check: no agent/*.py in bundle." >&2
  echo "Launcher bundle URL: $LAUNCHER_URL" >&2
  exit 1
fi
if ! grep -q "resolve_install_api" "$APP_HOME/agent/deferred_bundle_install.py" 2>/dev/null; then
  echo "Failed launcher self-check: installed deferred_bundle_install.py still missing resolve_install_api." >&2
  echo "APP_HOME=$APP_HOME" >&2
  echo "Deferred path: $APP_HOME/agent/deferred_bundle_install.py" >&2
  exit 1
fi
echo "Launcher bundle verified."

# Prefer $PREFIX/bin (Termux: on PATH). Always mkdir — do not rely on [ -w ]
USING_HOME_BIN=0
BIN=""
if [ -n "${PREFIX:-}" ]; then
  if mkdir -p "${PREFIX}/bin" 2>/dev/null; then
    BIN="${PREFIX}/bin"
  fi
fi
if [ -z "$BIN" ]; then
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

[ -s "$BIN/deng-rejoin" ] || _fail_install
[ -x "$BIN/deng-rejoin" ] || _fail_install
{ [ -f "$APP_HOME/.install_api" ] && [ -s "$APP_HOME/.install_api" ]; } || _fail_install
[ -f "$APP_HOME/agent/deng_tool_rejoin.py" ] || _fail_install
[ -f "$APP_HOME/agent/deferred_bundle_install.py" ] || _fail_install

DR_RESOLVED=""
if command -v deng-rejoin >/dev/null 2>&1; then
  DR_RESOLVED="$(command -v deng-rejoin)"
fi
if [ -z "$DR_RESOLVED" ]; then
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
if [ "$_PY_RC" -ne 0 ]; then
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
if [ -z "$API_R" ]; then
  echo "Failed: could not resolve install API (resolve_install_api)." >&2
  _fail_install
fi
if [ "$API_R" != "$DENG_REJOIN_INSTALL_API" ]; then
  echo "Install API URL mismatch after resolve." >&2
  echo "Expected: $DENG_REJOIN_INSTALL_API" >&2
  echo "Got: $API_R" >&2
  exit 1
fi

echo "Wrapper path: $BIN/deng-rejoin"
echo "command -v deng-rejoin -> $DR_RESOLVED"
echo "resolve_install_api (env unset) -> $API_R"
if [ "$USING_HOME_BIN" -eq 1 ]; then
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
        "#!/usr/bin/env sh\n"
        # POSIX-sh only.  No bash features anywhere — Termux's /usr/bin/sh
        # is dash, and the shebang on line 1 is ignored when invoked as
        # ``curl ... | sh``.  See module docstring.
        "set -eu\n"
        + f'echo "{safe_title}"\n'
        +         f"{banner_part}"
        + f'export DENG_REJOIN_INSTALL_API="{base}"\n'
        f"{sess_export}\n"
        'APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"\n'
        'mkdir -p "$APP_HOME"\n'
        f'printf \'%s\\n\' "{safe_req}" > "$APP_HOME/.install_requested"\n'
        'printf \'%s\\n\' "$DENG_REJOIN_INSTALL_API" > "$APP_HOME/.install_api"\n'
        'if [ -n "${BOOTSTRAP_SESSION:-}" ]; then\n'
        '  printf \'%s\\n\' "$BOOTSTRAP_SESSION" > "$APP_HOME/.bootstrap_session"\n'
        "else\n"
        '  rm -f "$APP_HOME/.bootstrap_session"\n'
        "fi\n"
        f"{tail.lstrip()}"
    )


def render_direct_install_bootstrap(
    *,
    base_url: str,
    package_sha256: str,
    banner_lines: tuple[str, ...] = (),
) -> str:
    """Generate a compact POSIX installer for the protected test package."""
    base = base_url.rstrip("/")
    safe_sha = _escape_double(package_sha256)

    pre_heredoc = (
        'echo "DENG Tool: Rejoin Installing"\n'
        'echo "Version: main-dev"\n'
        'command -v curl >/dev/null 2>&1 || { echo "Install curl first: pkg install -y curl" >&2; exit 1; }\n'
        'command -v tar >/dev/null 2>&1 || { echo "Install tar first: pkg install -y tar" >&2; exit 1; }\n'
        'command -v python3 >/dev/null 2>&1 || { echo "Install python first: pkg install -y python" >&2; exit 1; }\n'
        f'u="{base}";export DENG_REJOIN_INSTALL_API="$u"\n'
        'h="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}";mkdir -p "$h"\n'
        f's="{safe_sha}"\n'
        't="$(mktemp)";j="$(mktemp)";trap \'rm -f "$t" "$j"\' EXIT\n'
        'c="$(date +%s)-$$"\n'
        'curl -fsSL -H "Cache-Control: no-cache" -H "Pragma: no-cache" -A "deng-rejoin-installer/2.0" "$u/install/test/package-token?t=$c" -o "$j" || { echo "Download setup failed." >&2; exit 1; }\n'
        'p="$(python3 -c \'import json,sys;print(json.load(open(sys.argv[1])).get("url",""))\' "$j" 2>/dev/null)" || p=""\n'
        '[ -n "$p" ] || { echo "Download setup failed." >&2; exit 1; }\n'
        'curl -fsSL -H "Cache-Control: no-cache" -H "Pragma: no-cache" -A "deng-rejoin-installer/2.0" "$p" -o "$t" || { echo "Package download failed." >&2; exit 1; }\n'
        'a="$(python3 -c \'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())\' "$t" 2>/dev/null)" || a=""\n'
        '[ "$a" = "$s" ] || { echo "Package checksum mismatch." >&2; exit 1; }\n'
        'if command -v pkill >/dev/null 2>&1; then pkill -f "agent/deng_tool_rejoin.py" 2>/dev/null || true; fi\n'
        'for d in "$h"/a?ent "$h"/b?t "$h"/scr?pts "$h"/d?cs "$h"/ex?mples "$h"/assets "$h"/s?te "$h"/ser?er "$h"/te?ts; do rm -rf "$d" 2>/dev/null || true; done\n'
        'rm -f "$h/BUILD-INFO.json" "$h/RELEASE-MANIFEST.json" "$h/.installed-build.json" 2>/dev/null || true\n'
        'find "$h" -depth -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true\n'
        'find "$h" -name "*.pyc" 2>/dev/null -exec rm -f {} + || true\n'
        'tar -xzf "$t" -C "$h" || { echo "Could not extract package." >&2; exit 1; }\n'
        'find "$h" -depth -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true\n'
        'find "$h" -name "*.pyc" 2>/dev/null -exec rm -f {} + || true\n'
        '[ -f "$h/agent/deng_tool_rejoin.py" ] || { echo "Install error: runtime missing." >&2; exit 1; }\n'
        '[ -f "$h/agent/.deng_runtime.bin" ] || { echo "Install error: protected runtime missing." >&2; exit 1; }\n'
        '[ -f "$h/BUILD-INFO.json" ] || { echo "Install error: BUILD-INFO.json missing." >&2; exit 1; }\n'
        '[ -f "$h/RELEASE-MANIFEST.json" ] || { echo "Install error: RELEASE-MANIFEST.json missing." >&2; exit 1; }\n'
        'printf \'%s\\n\' "$u" > "$h/.install_api"\n'
        '_GIT_COMMIT="$(python3 -c \'import json,os,sys; p=sys.argv[1]; '
        'print((json.load(open(p)).get("git_commit","")) if os.path.isfile(p) else "")\' '
        '"$h/BUILD-INFO.json" 2>/dev/null)" || _GIT_COMMIT=""\n'
        '_PROBE_ID="$(python3 -c \'import json,os,sys; p=sys.argv[1]; '
        'print((json.load(open(p)).get("probe_id","")) if os.path.isfile(p) else "")\' '
        '"$h/BUILD-INFO.json" 2>/dev/null)" || _PROBE_ID=""\n'
        '_FILE_COUNT="$(find "$h/agent" -type f | wc -l 2>/dev/null | tr -d "[:space:]" || echo 0)"\n'
        '_INSTALL_TIME_ISO="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"\n'
        'cat > "$h/.installed-build.json" <<EOF\n'
        '{\n'
        '  "artifact_sha256": "$s",\n'
        '  "git_commit": "$_GIT_COMMIT",\n'
        '  "probe_id": "$_PROBE_ID",\n'
        '  "channel": "main-dev",\n'
        '  "install_time_iso": "$_INSTALL_TIME_ISO",\n'
        '  "install_api": "$u",\n'
        '  "package_url": "tokenized",\n'
        '  "installer_url": "$u/install/test/latest",\n'
        '  "extracted_file_count": $_FILE_COUNT\n'
        '}\n'
        'EOF\n'
        "USING_HOME_BIN=0\n"
        'BIN=""\n'
        'if [ -n "${PREFIX:-}" ]; then\n'
        '  if mkdir -p "${PREFIX}/bin" 2>/dev/null; then\n'
        '    BIN="${PREFIX}/bin"\n'
        "  fi\n"
        "fi\n"
        'if [ -z "$BIN" ]; then\n'
        '  BIN="$HOME/bin"\n'
        '  mkdir -p "$BIN"\n'
        '  export PATH="$HOME/bin:$PATH"\n'
        "  USING_HOME_BIN=1\n"
        "  _MARK='# DENG Tool: Rejoin - PATH (added by installer)'\n"
        "  _LINE='export PATH=\"$HOME/bin:$PATH\"'\n"
        '  for _rc in "$HOME/.bashrc" "$HOME/.profile"; do\n'
        '    touch "$_rc"\n'
        '    if ! grep -qF "$_MARK" "$_rc" 2>/dev/null; then\n'
        "      printf '\\n%s\\n%s\\n' \"$_MARK\" \"$_LINE\" >> \"$_rc\"\n"
        "    fi\n"
        "  done\n"
        "fi\n"
        'rm -f "$BIN/deng-rejoin" 2>/dev/null || true\n'
        "cat > \"$BIN/deng-rejoin\" << 'DENG_REJOIN_WRAPPER'\n"
    )

    post_heredoc = (
        "DENG_REJOIN_WRAPPER\n"
        "chmod +x \"$BIN/deng-rejoin\" || { echo \"chmod failed: $BIN/deng-rejoin\" >&2; exit 1; }\n"
        "hash -r 2>/dev/null || true\n"
        '[ -s "$BIN/deng-rejoin" ] || { echo "Failed to create deng-rejoin wrapper." >&2; exit 1; }\n'
        '[ -x "$BIN/deng-rejoin" ] || { echo "Failed: wrapper not executable." >&2; exit 1; }\n'
        '[ -f "$h/agent/deng_tool_rejoin.py" ] || { echo "Failed: runtime missing." >&2; exit 1; }\n'
        '[ -f "$h/BUILD-INFO.json" ] || { echo "Failed: BUILD-INFO.json missing in package." >&2; exit 1; }\n'
        '[ -f "$h/.installed-build.json" ] || { echo "Failed: .installed-build.json was not written." >&2; exit 1; }\n'
        'if ! PYTHONPATH="$h" python3 -c '
        "'import agent.commands, agent.supervisor, agent.roblox_presence, agent.freeform_enable, agent.playing_state, agent.dumpsys_cache, agent.window_apply' "
        '2>/dev/null; then\n'
        '  echo "Install verification failed: required modules did not import." >&2\n'
        "  exit 1\n"
        "fi\n"
        '_VERSION_OUT="$("$BIN/deng-rejoin" version 2>/dev/null)" || _VERSION_OUT=""\n'
        'if ! echo "$_VERSION_OUT" | grep -q "^artifact_sha256: " ; then\n'
        '  echo "Install verification failed: deng-rejoin version did not return artifact SHA." >&2\n'
        "  exit 1\n"
        "fi\n"
        '_INSTALLED_SHORT_SHA="$(echo "$_VERSION_OUT" | grep "^artifact_sha256: " | head -n1 | awk \'{print $2}\')"\n'
        "_EXPECTED_SHORT_SHA=\"$(printf '%.12s' \"$s\")\"\n"
        'if [ "$_INSTALLED_SHORT_SHA" != "$_EXPECTED_SHORT_SHA" ]; then\n'
        '  echo "Install verification failed: installed SHA mismatch." >&2\n'
        "  exit 1\n"
        "fi\n"
        '_LEGACY_IMPORT="NO"\n'
        '_SV_FILE="$h/agent/supervisor.py"\n'
        'if [ -f "$_SV_FILE" ]; then\n'
        '  if grep -qE "^[[:space:]]*(from|import)[^#]*experience_detector" "$_SV_FILE" 2>/dev/null; then\n'
        '    _LEGACY_IMPORT="YES (WARNING: old detector in supervisor)"\n'
        '  fi\n'
        'fi\n'
        '_AGENT_FILE="$(PYTHONPATH="$h" python3 -c "import agent; print(agent.__file__)" 2>/dev/null)" || _AGENT_FILE=""\n'
        'find "$h" -depth -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true\n'
        'find "$h" -name "*.pyc" 2>/dev/null -exec rm -f {} + || true\n'
        'echo "Install complete."\n'
    )

    return (
        "#!/usr/bin/env sh\n"
        # POSIX-sh only.  See module docstring: Termux's /usr/bin/sh is
        # dash, the shebang is ignored when invoked via ``curl ... | sh``,
        # and any bash feature would abort with "Illegal option" or
        # "binary file" errors before this script gets to do anything useful.
        + "set -eu\n"
        + pre_heredoc
        + wrapper_body_sh(base)
        + post_heredoc
    )


def _escape_double(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


# ── POSIX-sh discipline ─────────────────────────────────────────────────────
#
# We previously attempted a "re-exec into bash" preamble.  It failed on the
# real device:
#
#     $ curl -fsSL https://rejoin.deng.my.id/install/latest | sh
#     /data/data/com.termux/files/usr/bin/sh: /data/data/com.termux/files/usr/bin/sh: cannot execute binary file
#
# Root cause: when ``curl ... | sh`` runs on Termux, dash's ``$0`` is the
# **dash binary itself** (``/data/data/com.termux/files/usr/bin/sh``), not
# the name of a script file.  Our preamble did ``exec bash "$0"``, which
# made bash try to execute the dash ELF binary as a shell script.
#
# Lesson learned: do not try to be clever.  Both bootstrap renderers above
# emit **strictly POSIX-sh** scripts: ``set -eu`` only, ``[ ... ]`` only,
# ``case`` instead of ``[[ == *pattern* ]]``, ``printf '%.Ns'`` instead of
# ``${VAR:0:N}``, explicit glob loops instead of arrays + ``shopt nullglob``.
# These run cleanly under dash, busybox sh, ash, bash, and any other POSIX
# shell.  No re-exec, no temp files, no PATH lookups.
