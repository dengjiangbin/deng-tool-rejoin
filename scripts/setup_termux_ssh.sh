#!/usr/bin/env bash
# setup_termux_ssh.sh — set up OpenSSH on Termux for live-debug access.
#
# What this does on the phone:
#   1. Install openssh / openssh-sftp-server.
#   2. Generate an ed25519 keypair at ~/.ssh/deng_phone.
#   3. Configure sshd to listen on 127.0.0.1:8022 only (no public exposure).
#   4. Print the public key so the operator can install it in their
#      panel-host authorized_keys.
#   5. Print the one-line reverse-tunnel command for daily use.
#
# Re-runnable: idempotent. Existing key is preserved.
#
# Usage:
#   bash scripts/setup_termux_ssh.sh <panel-host>
#
# After setup you launch the tunnel with:
#   ssh -i $HOME/.ssh/deng_phone -N -R 2222:127.0.0.1:8022 tunnel@<panel-host>

set -u

PANEL_HOST="${1:-}"

if ! command -v pkg >/dev/null 2>&1; then
  echo "[error] this script must run inside Termux (pkg not found)" >&2
  exit 1
fi

echo "[1/5] installing openssh ..."
pkg install -y openssh openssh-sftp-server >/dev/null 2>&1 || {
  echo "[error] pkg install failed; check network" >&2
  exit 1
}

KEY="$HOME/.ssh/deng_phone"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

echo "[2/5] preparing keypair at $KEY ..."
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -f "$KEY" -N "" -C "deng-phone-$(date -u +%Y%m%d)" >/dev/null
  echo "       generated new ed25519 keypair"
else
  echo "       existing key reused"
fi
chmod 600 "$KEY"

SSHD_CONF="$PREFIX/etc/ssh/sshd_config"
if [ -f "$SSHD_CONF" ]; then
  echo "[3/5] hardening $SSHD_CONF ..."
  # Listen on localhost only — the public exposure happens via the reverse
  # tunnel on the panel host, not from the phone directly.
  if ! grep -q "^ListenAddress 127.0.0.1" "$SSHD_CONF" 2>/dev/null; then
    {
      echo ""
      echo "# deng-rejoin live-debug:"
      echo "ListenAddress 127.0.0.1"
      echo "Port 8022"
      echo "PasswordAuthentication no"
      echo "PermitRootLogin no"
    } >> "$SSHD_CONF"
  fi
fi

# Make sure our key is allowed to log in to this Termux instance — useful
# when we tunnel *outbound* and the operator hops back through, but only
# from localhost.
AUTH="$HOME/.ssh/authorized_keys"
touch "$AUTH"
chmod 600 "$AUTH"
PUB=$(cat "${KEY}.pub")
if ! grep -qxF "$PUB" "$AUTH"; then
  echo "$PUB" >> "$AUTH"
fi

echo "[4/5] starting sshd ..."
pkill -x sshd 2>/dev/null || true
sshd

echo
echo "=========================================="
echo "  PHONE → PANEL HOST PUBLIC KEY (copy this)"
echo "=========================================="
cat "${KEY}.pub"
echo

if [ -n "$PANEL_HOST" ]; then
  echo "[5/5] daily one-liner (run this when you want the operator to connect):"
  echo
  echo "  ssh -i $KEY -N -R 2222:127.0.0.1:8022 tunnel@$PANEL_HOST"
  echo
  echo "Operator then connects locally on the panel host with:"
  echo
  echo "  ssh -p 2222 $USER@127.0.0.1"
else
  echo "[5/5] daily one-liner (replace <panel-host> with the real address):"
  echo
  echo "  ssh -i $KEY -N -R 2222:127.0.0.1:8022 tunnel@<panel-host>"
fi
echo
echo "Done. The phone is reachable only over your reverse tunnel — kill"
echo "the ssh process or run \`pkill sshd\` to close access."
