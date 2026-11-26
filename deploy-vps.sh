#!/usr/bin/env bash
# DENG Rejoin — one-shot deploy for an Ubuntu/Debian VPS (DigitalOcean).
# Paste into your VPS shell (Termius). Re-run anytime to update to the latest code.
# Puts the app under pm2 (auto-restart, boot-start) behind Caddy (automatic HTTPS).
set -e
DOMAIN="rejoin.deng.my.id"
APPDIR="/opt/deng-rejoin"
PORT=8080
REPO="https://github.com/dengjiangbin/deng-tool-rejoin"

echo "== 1/6 base packages =="
sudo apt-get update -y
command -v node >/dev/null 2>&1 || { curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -; sudo apt-get install -y nodejs; }
sudo apt-get install -y git build-essential debian-keyring debian-archive-keyring apt-transport-https curl
# Caddy (automatic HTTPS reverse proxy)
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y && sudo apt-get install -y caddy
fi

echo "== 2/6 fetch app =="
sudo mkdir -p "$APPDIR"; sudo chown -R "$USER" "$APPDIR"
rm -rf /tmp/deng && git clone --depth 1 "$REPO" /tmp/deng
rsync -a --delete --exclude node_modules --exclude data /tmp/deng/server/ "$APPDIR"/

echo "== 3/6 install deps =="
cd "$APPDIR"
npm install --omit=dev

echo "== 4/6 env (kept across re-runs) =="
if [ ! -f "$APPDIR/.env" ]; then
  cat > "$APPDIR/.env" <<EOF
JWT_SECRET=$(openssl rand -hex 32)
PUBLIC_URL=https://$DOMAIN
PORT=$PORT
# Optional email for password resets — fill these to enable:
# SMTP_HOST=
# SMTP_PORT=587
# SMTP_USER=
# SMTP_PASS=
# MAIL_FROM=DENG Rejoin <no-reply@deng.my.id>
EOF
  echo "  wrote $APPDIR/.env (random JWT_SECRET)"
fi

echo "== 5/6 pm2 process =="
sudo npm i -g pm2 >/dev/null 2>&1 || true
cat > "$APPDIR/ecosystem.config.js" <<EOF
module.exports = { apps: [{ name: 'deng-rejoin', script: 'server.js', cwd: '$APPDIR',
  node_args: '--env-file=$APPDIR/.env', autorestart: true, max_restarts: 20, env: { NODE_ENV: 'production' } }] };
EOF
pm2 start "$APPDIR/ecosystem.config.js" --update-env || pm2 restart deng-rejoin --update-env
pm2 save
sudo env PATH=$PATH pm2 startup systemd -u "$USER" --hp "$HOME" | tail -1 | sudo bash || true

echo "== 6/6 Caddy reverse proxy (auto HTTPS) =="
echo "$DOMAIN {
    reverse_proxy localhost:$PORT
}" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl enable caddy
sudo systemctl restart caddy

echo ""
echo "Deployed. App on :$PORT behind Caddy for https://$DOMAIN"
echo "Next: point DNS for $DOMAIN at this server's IP (see Cloudflare steps)."
pm2 status
