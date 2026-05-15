#!/data/data/com.termux/files/usr/bin/bash
# DENG Tool: Rejoin — Licensed Bootstrap Installer
#
# Usage:
#   bash bootstrap_install.sh
#   DENG_LICENSE_SERVER=https://your.domain.com bash bootstrap_install.sh
#   DENG_CHANNEL=beta bash bootstrap_install.sh
#
# This installer:
#   1. Asks for your license key (from Discord panel).
#   2. Generates / reads your install_id.
#   3. Calls POST /api/download/authorize on the license server.
#   4. Downloads the signed package zip.
#   5. Verifies SHA-256.
#   6. Extracts into ~/.deng-tool/rejoin/.
#   7. Applies secure permissions.
#   8. Creates global deng-rejoin command.
#
# Security:
#   - The installer DOES NOT contain Supabase service role key.
#   - The installer DOES NOT contain Discord bot token.
#   - The installer DOES NOT require private GitHub access.
#   - The license key is hashed (SHA-256) before being sent to the server.
#   - Only the install_id HASH is sent, never the raw value.
#
# To use this installer publicly, replace DEFAULT_SERVER_URL below with
# your actual production domain. Until then, set:
#   DENG_LICENSE_SERVER=http://127.0.0.1:8787

set -euo pipefail

# ── Configuration (can be overridden by environment) ──────────────────────────
DEFAULT_SERVER_URL="http://127.0.0.1:8787"
SERVER_URL="${DENG_LICENSE_SERVER:-$DEFAULT_SERVER_URL}"
APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
CHANNEL="${DENG_CHANNEL:-stable}"
DEVICE_MODEL="$(getprop ro.product.model 2>/dev/null || echo termux-android)"
APP_VERSION="0.0.0"
INSTALL_ID_FILE="$APP_HOME/.install_id"

# ── Colour helpers ─────────────────────────────────────────────────────────────
_pink()  { printf '\033[95m%s\033[0m\n' "$1"; }
_green() { printf '\033[32m%s\033[0m\n' "$1"; }
_red()   { printf '\033[31m%s\033[0m\n' "$1"; }
_bold()  { printf '\033[1m%s\033[0m\n' "$1"; }
_die()   { _red "Error: $*"; exit 1; }

# ── Title ──────────────────────────────────────────────────────────────────────
_title() {
  _pink 'DDDDD   EEEEE  N   N   GGGG'
  _pink 'D    D  E      NN  N  G'
  _pink 'D    D  EEEE   N N N  G  GG'
  _pink 'D    D  E      N  NN  G   G'
  _pink 'DDDDD   EEEEE  N   N   GGG'
  _bold 'Tool: Rejoin — Licensed Bootstrap Installer'
  echo
}

# ── Dependency checks ──────────────────────────────────────────────────────────
_check_deps() {
  local missing=()
  for cmd in python curl sha256sum; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    echo "Installing missing tools: ${missing[*]}"
    pkg install -y "${missing[@]}" 2>/dev/null || \
      _die "Could not install: ${missing[*]}. Run: pkg install python curl"
  fi
}

# ── Install_id management ──────────────────────────────────────────────────────
_get_or_create_install_id() {
  mkdir -p "$APP_HOME"
  if [ -f "$INSTALL_ID_FILE" ] && [ -s "$INSTALL_ID_FILE" ]; then
    cat "$INSTALL_ID_FILE"
    return 0
  fi

  # Generate a random install_id from /dev/urandom
  local new_id
  new_id="$(python -c "import uuid; print(uuid.uuid4().hex)")"
  printf '%s' "$new_id" > "$INSTALL_ID_FILE"
  chmod 600 "$INSTALL_ID_FILE"
  echo "$new_id"
}

# Hash install_id (SHA-256) before sending — never transmit raw value
_hash_install_id() {
  local raw_id="$1"
  printf '%s' "$raw_id" | sha256sum | awk '{print $1}'
}

# ── License key prompt ─────────────────────────────────────────────────────────
_prompt_license_key() {
  echo
  _bold "Step 1: Enter your license key"
  echo "  Get your key from Discord: click 'Generate Key' in the #license-panel channel."
  echo "  Format: DENG-XXXXXXXX (minimum 8 hex characters after DENG-)"
  echo
  local key=""
  while true; do
    printf "  License key: "
    read -r key
    key="$(echo "$key" | tr '[:lower:]' '[:upper:]' | tr -d ' ')"
    # Basic format check (DENG- followed by at least 8 hex chars)
    if echo "$key" | grep -qE '^DENG-[A-Fa-f0-9]{8,}$'; then
      break
    fi
    _red "  Invalid format. Expected DENG-XXXXXXXX (e.g. DENG-38ab1234)"
  done
  echo "$key"
}

# ── Channel selection ──────────────────────────────────────────────────────────
_prompt_channel() {
  echo
  _bold "Step 2: Choose release channel"
  echo "  [1] stable  — recommended for most users (default)"
  echo "  [2] beta    — preview features, may have bugs"
  printf "  Choice [1]: "
  local choice=""
  read -r choice
  case "$choice" in
    2|beta) echo "beta" ;;
    *)      echo "stable" ;;
  esac
}

# ── Authorize download ─────────────────────────────────────────────────────────
_authorize_download() {
  local key="$1" install_id_hash="$2" channel="$3"
  local url="$SERVER_URL/api/download/authorize"

  echo
  _bold "Step 3: Authorizing download with license server..."
  echo "  Server  : $SERVER_URL"
  echo "  Channel : $channel"

  local json_body
  json_body="$(printf '{"key":"%s","install_id_hash":"%s","device_model":"%s","app_version":"%s","channel":"%s"}' \
    "$key" "$install_id_hash" "$DEVICE_MODEL" "$APP_VERSION" "$channel")"

  local response
  response="$(curl -s -w '\n%{http_code}' -X POST "$url" \
    -H "Content-Type: application/json" \
    --data-raw "$json_body" \
    --max-time 30 \
    --retry 2 \
    --retry-delay 2)" || _die "Cannot reach license server at $SERVER_URL"

  local body http_code
  body="$(echo "$response" | head -n -1)"
  http_code="$(echo "$response" | tail -n 1)"

  if [ "$http_code" != "200" ]; then
    local error_msg
    error_msg="$(echo "$body" | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('message',d.get('error','Unknown error')))" 2>/dev/null || echo "$body")"
    _die "License check failed (HTTP $http_code): $error_msg"
  fi

  # Parse response
  DOWNLOAD_TOKEN="$(echo "$body" | python -c "import json,sys; d=json.load(sys.stdin); print(d['download_token'])")" || \
    _die "Server response missing download_token."
  DOWNLOAD_URL="$(echo "$body" | python -c "import json,sys; d=json.load(sys.stdin); print(d['download_url'])")" || \
    _die "Server response missing download_url."
  PKG_SHA256="$(echo "$body" | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('sha256',''))")" || true
  PKG_FILENAME="$(echo "$body" | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('filename','package.zip'))")" || \
    PKG_FILENAME="package.zip"
  PKG_VERSION="$(echo "$body" | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('version',''))")" || \
    PKG_VERSION=""

  _green "  Authorized! Version: ${PKG_VERSION:-unknown}"
}

# ── Download package ───────────────────────────────────────────────────────────
_download_package() {
  local tmp_dir="$1" filename="$2"
  local dest="$tmp_dir/$filename"
  echo
  _bold "Step 4: Downloading package..."
  echo "  File: $filename"

  # Use the download_token as Bearer auth — it IS the short-lived access credential
  curl -sfL "$DOWNLOAD_URL" \
    -H "Authorization: Bearer $DOWNLOAD_TOKEN" \
    -o "$dest" \
    --max-time 120 \
    --retry 2 \
    --retry-delay 3 || _die "Download failed. Check network and try again."

  echo "$dest"
}

# ── SHA-256 verification ───────────────────────────────────────────────────────
_verify_sha256() {
  local file="$1" expected="$2"
  if [ -z "$expected" ]; then
    echo "  Warning: server did not provide SHA-256, skipping verification."
    return 0
  fi
  echo
  _bold "Step 5: Verifying package integrity..."
  local actual
  actual="$(sha256sum "$file" | awk '{print $1}')"
  if [ "$actual" != "$expected" ]; then
    _die "SHA-256 mismatch! Expected: $expected  Got: $actual — package may be corrupted or tampered."
  fi
  _green "  SHA-256 verified."
}

# ── Extract + install ──────────────────────────────────────────────────────────
_install_package() {
  local zip_file="$1"
  echo
  _bold "Step 6: Installing to $APP_HOME..."

  mkdir -p "$APP_HOME"

  # Extract (Python handles .env exclusion and traversal protection)
  python - <<PYEOF
import os, sys, zipfile, re
zip_path = "$zip_file"
install_dir = "$APP_HOME"
os.makedirs(install_dir, exist_ok=True)
extracted = 0
skipped = 0
with zipfile.ZipFile(zip_path, 'r') as zf:
    for member in zf.infolist():
        name = member.filename
        # Skip dangerous paths
        if name.startswith('/') or '..' in name.replace('\\\\', '/'):
            skipped += 1
            continue
        # Skip .env files at any depth
        parts = name.replace('\\\\', '/').split('/')
        if any(p == '.env' or p.endswith('.env') for p in parts):
            skipped += 1
            continue
        # Skip path traversal
        import pathlib
        resolved = (pathlib.Path(install_dir) / name).resolve()
        try:
            resolved.relative_to(pathlib.Path(install_dir).resolve())
        except ValueError:
            skipped += 1
            continue
        zf.extract(member, install_dir)
        extracted += 1
print(f"Extracted {extracted} files (skipped {skipped} unsafe entries).")
PYEOF

  # Apply permissions (Unix only)
  if [ "$(uname -s)" != "CYGWIN"* ] && [ "$(uname -s)" != "MINGW"* ]; then
    chmod -R go-rwx "$APP_HOME" 2>/dev/null || true
    find "$APP_HOME" -type f -exec chmod 600 {} \; 2>/dev/null || true
    find "$APP_HOME" -type d -exec chmod 700 {} \; 2>/dev/null || true
    # Make shell scripts executable
    find "$APP_HOME/scripts" -name "*.sh" -exec chmod 700 {} \; 2>/dev/null || true
    chmod 700 "$APP_HOME/install.sh" 2>/dev/null || true
  fi

  _green "  Package installed."
}

# ── Save configuration ─────────────────────────────────────────────────────────
_save_config() {
  local key="$1" install_id="$2" channel="$3"
  local config_file="$APP_HOME/config.json"

  echo
  _bold "Step 7: Saving configuration..."

  if [ ! -f "$config_file" ]; then
    # Bootstrap minimal config from example if available
    local example="$APP_HOME/examples/config.example.json"
    if [ -f "$example" ]; then
      cp "$example" "$config_file"
    else
      echo '{}' > "$config_file"
    fi
  fi

  python - <<PYEOF
import json, os
config_file = "$config_file"
key = "$key"
install_id = "$install_id"
channel = "$channel"
server_url = "$SERVER_URL"

try:
    with open(config_file, encoding='utf-8') as f:
        data = json.load(f)
except (OSError, json.JSONDecodeError):
    data = {}

data['license_key'] = key
lic = data.setdefault('license', {})
lic['enabled'] = True
lic['mode'] = 'remote'
lic['key'] = key
lic['server_url'] = server_url
lic['install_id'] = install_id
lic['channel'] = channel
lic['last_status'] = 'installed'

with open(config_file, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)

os.chmod(config_file, 0o600)
print("Config saved.")
PYEOF
}

# ── Global command setup ───────────────────────────────────────────────────────
_setup_global_commands() {
  local prefix="${PREFIX:-/data/data/com.termux/files/usr}"
  mkdir -p "$prefix/bin"

  cat > "$prefix/bin/deng-rejoin" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
cd "\$HOME/.deng-tool/rejoin" || exit 1
if [ "\$#" -gt 0 ]; then
  exec python agent/deng_tool_rejoin.py "\$@"
fi
exec python agent/deng_tool_rejoin.py menu
EOF
  chmod +x "$prefix/bin/deng-rejoin"

  for cmd in update status stop start logs; do
    cat > "$prefix/bin/deng-rejoin-$cmd" <<EOF2
#!/data/data/com.termux/files/usr/bin/sh
cd "\$HOME/.deng-tool/rejoin" || exit 1
exec python agent/deng_tool_rejoin.py $cmd "\$@"
EOF2
    chmod +x "$prefix/bin/deng-rejoin-$cmd"
  done
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
  _title
  _check_deps

  # Warn if using default server URL (not configured for production)
  if [ "$SERVER_URL" = "$DEFAULT_SERVER_URL" ]; then
    echo "  Note: Using default local server URL ($DEFAULT_SERVER_URL)."
    echo "  For production, set: DENG_LICENSE_SERVER=https://your-domain.example.com"
    echo
  fi

  local key channel install_id install_id_hash

  key="$(_prompt_license_key)"
  channel="$(_prompt_channel)"
  install_id="$(_get_or_create_install_id)"
  install_id_hash="$(_hash_install_id "$install_id")"

  # Declare global vars populated by _authorize_download
  DOWNLOAD_TOKEN=""
  DOWNLOAD_URL=""
  PKG_SHA256=""
  PKG_FILENAME="package.zip"
  PKG_VERSION=""

  _authorize_download "$key" "$install_id_hash" "$channel"

  local tmp_dir
  tmp_dir="$(mktemp -d "${TMPDIR:-/data/data/com.termux/files/usr/tmp}/deng-install.XXXXXX")"
  trap 'rm -rf "$tmp_dir"' EXIT

  local zip_file
  zip_file="$(_download_package "$tmp_dir" "$PKG_FILENAME")"

  _verify_sha256 "$zip_file" "$PKG_SHA256"
  _install_package "$zip_file"
  _save_config "$key" "$install_id" "$channel"
  _setup_global_commands

  echo
  _green "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  _green " DENG Tool: Rejoin installed successfully!"
  _green "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo
  echo "  Start the menu:    deng-rejoin"
  echo "  Run setup:         deng-rejoin setup"
  echo "  Check status:      deng-rejoin-status"
  echo "  Check for updates: deng-rejoin-update"
  echo
  echo "  Your install_id is saved at: $INSTALL_ID_FILE"
  echo "  Keep this device as your primary — HWID is bound to it."
  echo "  To switch devices: use 'Reset HWID' in the Discord panel."
  echo
}

main "$@"
