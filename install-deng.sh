#!/data/data/com.termux/files/usr/bin/bash
# One-time Termux setup so you can launch everything with:  start deng
# Optional: a setup code from the website signs this cloud phone in automatically:
#   curl -fsSL .../install-deng.sh | bash -s -- CODE
set -u
SETUP_CODE="${1:-}"
SERVER="https://deng-rejoin.discloud.dev"
RAW="https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
BIN="$PREFIX/bin"
DENG="$HOME/.deng"
mkdir -p "$DENG"

echo 'Installing DENG tools...'
pkg install -y curl jq coreutils openssl-tool gzip >/dev/null 2>&1 || echo 'Note: install curl jq coreutils openssl-tool gzip manually if this failed.'

# fetch launcher + components
curl -fsSL "$RAW/deng-launch.sh" -o "$DENG/deng-launch.sh" && chmod +x "$DENG/deng-launch.sh" || { echo 'Failed to download launcher.'; exit 1; }
curl -fsSL "$RAW/code616.sh"      -o "$HOME/code616.sh"      && chmod +x "$HOME/code616.sh"
curl -fsSL "$RAW/deng-agent.sh"   -o "$HOME/deng-agent.sh"   && chmod +x "$HOME/deng-agent.sh"

# install the `start` command so `start deng` works
cat > "$BIN/start" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
case "\${1:-}" in
  deng) exec bash "$DENG/deng-launch.sh" "\${@:2}" ;;
  *) echo 'usage: start deng' ;;
esac
EOF
chmod +x "$BIN/start"

# open DENG automatically whenever Termux starts (turn off in the DENG menu)
bash "$DENG/deng-launch.sh" --autostart-install >/dev/null 2>&1 || true

# sign in with the website's setup code: no username/password needed
if [ -n "$SETUP_CODE" ]; then
  out=$(curl -sS -m 20 -w '\n%{http_code}' -X POST -H 'Content-Type: application/json' \
        --data-binary "{\"code\":\"$(printf '%s' "$SETUP_CODE" | tr -cd 'A-Za-z0-9')\"}" "$SERVER/api/setup-code/redeem" 2>/dev/null) || out=$'\n000'
  code=${out##*$'\n'}; body=${out%$'\n'*}
  if [ "$code" = 200 ]; then
    tok=$(printf '%s' "$body" | jq -r '.token // empty')
    ( umask 077; printf '%s' "$tok" > "$DENG/auth"; printf '%s' "$SERVER" > "$DENG/server" )
    echo "Signed in as $(printf '%s' "$body" | jq -r '.user.username')."
  else
    echo "Setup code not accepted: $(printf '%s' "$body" | jq -r '.error // "server unreachable"' 2>/dev/null). You can sign in with username and password instead."
  fi
fi

echo ''
echo 'Done. DENG now opens by itself when Termux starts. Or run:  start deng'
# open it right away when run from a Termux window
if [ -e /dev/tty ] && { : < /dev/tty; } 2>/dev/null; then
  export DENG_AUTO_DONE=1
  exec bash "$DENG/deng-launch.sh" < /dev/tty
fi
