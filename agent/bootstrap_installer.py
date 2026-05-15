"""Bootstrap shell script bodies served from GET /install/* (Termux-friendly)."""

from __future__ import annotations


def render_public_bootstrap(
    *,
    base_url: str,
    requested: str,
    bootstrap_session: str = "",
    installer_title: str = "DENG Tool: Rejoin Installer",
    banner_lines: tuple[str, ...] = (),
) -> str:
    """*requested* is ``latest``, ``v1.0.0``, ``main-dev``, ``test-latest``, etc.

    Empty *bootstrap_session* for public stable/latest and fixed internal test installers;
    legacy signed ``/install/dev/main`` bootstraps embed a session validated by
    ``POST /api/install/authorize``.
    """
    base = base_url.rstrip("/")
    if bootstrap_session:
        sess_line = f'export BOOTSTRAP_SESSION="{bootstrap_session}"'
    else:
        sess_line = "unset BOOTSTRAP_SESSION 2>/dev/null || true"

    banner_part = ""
    if banner_lines:
        banner_part = "\n".join(f'echo "{line}"' for line in banner_lines) + "\n"

    return f"""#!/usr/bin/env bash
set -euo pipefail
echo "{installer_title}"
{banner_part}if [[ -n "${{PREFIX:-}}" ]] && [[ "${{PREFIX}}" == *termux* ]]; then
  echo "Detected: Termux"
fi
export DENG_REJOIN_INSTALL_API="{base}"
REQUESTED="{requested}"
{sess_line}
command -v curl >/dev/null 2>&1 || {{ echo "Install curl first: pkg install -y curl" >&2; exit 1; }}
command -v python3 >/dev/null 2>&1 || {{ echo "Install python first: pkg install -y python" >&2; exit 1; }}

prompt_key() {{
  if [[ -n "${{DENG_LICENSE_KEY:-}}" ]]; then
    printf '%s' "$DENG_LICENSE_KEY"
    return
  fi
  read -r -s -p "License key (hidden): " K || true
  echo ""
  printf '%s' "$K"
}}

RAW_KEY="$(prompt_key)"
if [[ -z "${{RAW_KEY// }} ]]; then
  echo "A license key is required." >&2
  exit 1
fi

INSTALL_HASH="${{DENG_INSTALL_ID_HASH:-}}"
BODY="$(env REQUESTED="$REQUESTED" RAW_KEY="$RAW_KEY" INSTALL_HASH="$INSTALL_HASH" \\
  BOOTSTRAP_SESSION="${{BOOTSTRAP_SESSION:-}}" python3 - << 'PY'
import json, os
req = {{
    "license_key": os.environ["RAW_KEY"],
    "requested_version": os.environ["REQUESTED"],
    "install_id_hash": os.environ.get("INSTALL_HASH", ""),
}}
bs = (os.environ.get("BOOTSTRAP_SESSION") or "").strip()
if bs:
    req["bootstrap_session"] = bs
print(json.dumps(req))
PY
)"

RESP="$(curl -fsSL -X POST "$DENG_REJOIN_INSTALL_API/api/install/authorize" \\
  -H "Content-Type: application/json" \\
  -d "$BODY")" || {{ echo "Could not reach install server." >&2; exit 1; }}

RESULT="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('result',''))" "$RESP")" || true
if [[ "$RESULT" != "active" ]]; then
  MSG="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('message','Install denied'))" "$RESP" 2>/dev/null || echo Install denied)"
  echo "$MSG" >&2
  exit 1
fi

URL="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('download_url',''))" "$RESP")"
SUM="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('sha256','') or '')" "$RESP")"
VER="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('resolved_version',''))" "$RESP")"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$URL" -o "$TMP"

if [[ -n "$SUM" ]]; then
  if command -v sha256sum >/dev/null 2>&1; then
    GOT="$(sha256sum "$TMP" | awk '{{print $1}}')"
  else
    GOT="$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$TMP")"
  fi
  if [[ "$GOT" != "$SUM" ]]; then
    echo "SHA256 mismatch — aborting." >&2
    exit 1
  fi
fi

APP_HOME="${{DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}}"
mkdir -p "$APP_HOME"
if command -v tar >/dev/null 2>&1; then
  tar -xzf "$TMP" -C "$APP_HOME" 2>/dev/null || {{
    mkdir -p "$APP_HOME/vendor_extract"
    tar -xzf "$TMP" -C "$APP_HOME/vendor_extract" || {{ echo "Extract failed." >&2; exit 1; }}
  }}
else
  echo "tar not found — run: pkg install -y tar" >&2
  exit 1
fi

BIN="$HOME/.local/bin"
mkdir -p "$BIN"
LAUNCH="$APP_HOME/agent/deng_tool_rejoin.py"
if [[ -f "$LAUNCH" ]]; then
  cat > "$BIN/deng-rejoin" << EOF
#!/bin/sh
exec python3 "$LAUNCH" "$@"
EOF
  chmod +x "$BIN/deng-rejoin"
fi

echo ""
echo "Installed version: $VER"
echo "Next step: deng-rejoin"
"""
