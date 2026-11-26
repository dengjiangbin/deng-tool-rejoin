#!/data/data/com.termux/files/usr/bin/bash
# One-time Termux setup so you can launch everything with:  start deng
set -u
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

echo ''
echo 'Done. Launch anytime with:'
echo '    start deng'
