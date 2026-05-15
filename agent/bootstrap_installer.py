"""Bootstrap shell scripts for GET /install/* (Termux-friendly, LF-only).

Install is non-interactive: it downloads a small launcher tarball, extracts into
``~/.deng-tool/rejoin``, and writes ``.install_requested``. License-gated download
of the full bundle runs on first ``deng-rejoin`` (:mod:`agent.deferred_bundle_install`).
"""

from __future__ import annotations

_WRAPPER_BODY = """#!/bin/sh
export DENG_REJOIN_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
exec python3 "$DENG_REJOIN_HOME/agent/deng_tool_rejoin.py" "$@"
"""


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

    # shellcheck-friendly: newline after banner echoes before `if [[ ... ]]`
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
        'command -v curl >/dev/null 2>&1 || { echo "Install curl first: pkg install -y curl" >&2; exit 1; }\n'
        'command -v tar >/dev/null 2>&1 || { echo "Install tar first: pkg install -y tar" >&2; exit 1; }\n'
        'LAUNCHER_URL="$DENG_REJOIN_INSTALL_API/install/launcher/bundle.tar.gz"\n'
        'TMP="$(mktemp)"\n'
        "trap 'rm -f \"$TMP\"' EXIT\n"
        'curl -fsSL "$LAUNCHER_URL" -o "$TMP" || { echo "Could not download launcher bundle." >&2; exit 1; }\n'
        'tar -xzf "$TMP" -C "$APP_HOME"\n'
        'BIN="$HOME/.local/bin"\n'
        'mkdir -p "$BIN"\n'
        "cat > \"$BIN/deng-rejoin\" << 'DENG_REJOIN_WRAPPER'\n"
        f"{_WRAPPER_BODY}"
        "DENG_REJOIN_WRAPPER\n"
        'chmod +x "$BIN/deng-rejoin"\n'
        'echo ""\n'
        'echo "Install complete."\n'
        'echo "Next: run deng-rejoin"\n'
    )


def _escape_double(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')
