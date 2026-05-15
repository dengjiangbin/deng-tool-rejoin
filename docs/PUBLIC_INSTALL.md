# Public Install Guide

## Prepare First

1. Android cloud phone or Android device.
2. Termux installed.
3. Roblox installed.
4. Internet connection.
5. Termux storage permission.
6. Optional root permission for stronger restart.
7. Optional Termux:Boot for start after reboot.
8. Optional Roblox private-server URL or normal Roblox game URL.

DENG never asks for Roblox password, cookies, `.ROBLOSECURITY`, session tokens, or 2FA codes.

## Install With curl

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

## Install With wget

```sh
wget -O install.sh https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh && bash install.sh
```

## Start

```sh
deng-rejoin
```

Choose **First Time Setup Config**, then choose **Start**.

Setup uses a guided public menu. You will choose Roblox packages/account names, launch link, optional Discord webhook, and optional snapshot/webhook interval only when webhook is enabled. Window layout is automatic at Start when multiple packages are selected.

## Android 10

Some Android 10 images expose downloads as `/sdcard/download`; others use `/sdcard/Download`. DENG detects both and creates a launcher where possible:

```sh
python /sdcard/download/deng-rejoin.py
```

## Android 12+

Most Android 12+ images use `/sdcard/Download`. Background restrictions may stop Termux, so disable battery optimization when possible:

```sh
python /sdcard/Download/deng-rejoin.py
```

## Root Optional

Non-root mode can open Roblox or a Roblox URL. Root mode can force-stop Roblox before relaunching. Root commands are limited to safe app management and are timeout-protected.

## Termux:Boot Optional

```sh
deng-rejoin enable-boot
```

Install/open Termux:Boot once, disable battery optimization if possible, reboot, then run:

```sh
deng-rejoin-status
```

## Update

```sh
deng-rejoin-update
```

Fallback:

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

## Reset

```sh
deng-rejoin-reset
```

Reset keeps logs by default and asks before wiping database/logs.

## Uninstall

```sh
sh ~/.deng-tool/rejoin/scripts/uninstall.sh
```

## Troubleshooting

Run:

```sh
deng-rejoin doctor
```

Doctor checks Python, Termux, Android version, SDK, Download path, root, Roblox package, SQLite, logs, and duplicate agent state.

---

## License API — Self-Hosting Behind a Reverse Proxy

The License API exposes HTTP endpoints that Termux clients call to verify licenses and receive signed download packages.  In production the API must run behind HTTPS.

### Architecture

```
Phone / Termux
   ↓ HTTPS
https://yourdomain.com/rejoin-api
   ↓ proxy_pass
License API  (127.0.0.1:8787)
   ↓
Supabase
   ↓
Download package
```

### Required environment variables (`.env` / PM2 ecosystem)

| Variable | Example | Purpose |
|---|---|---|
| `LICENSE_API_ENABLED` | `true` | Enable the API thread |
| `LICENSE_API_HOST` | `127.0.0.1` | Bind address (loopback when behind proxy) |
| `LICENSE_API_PORT` | `8787` | Bind port |
| `LICENSE_API_PUBLIC_URL` | `https://yourdomain.com/rejoin-api` | Public URL returned to clients in `download_url`. Trailing slash optional. |
| `LICENSE_API_PATH_PREFIX` | `/rejoin-api` | If your proxy forwards `/rejoin-api/api/…` to `127.0.0.1:8787/rejoin-api/api/…` (no prefix strip), set this so the WSGI router strips it internally. Leave empty if your proxy strips the prefix before forwarding. |
| `LICENSE_API_SHARED_SECRET` | `<random>` | Optional Bearer token for extra auth between proxy and API |
| `LICENSE_DOWNLOAD_ROOT` | `/opt/deng/dist` | Absolute path to the `dist/releases/` folder |
| `LICENSE_DOWNLOAD_TOKEN_TTL_SECONDS` | `300` | Download token lifetime (seconds) |

### nginx — prefix strip (recommended)

```nginx
location /rejoin-api/ {
    proxy_pass         http://127.0.0.1:8787/;  # trailing slash strips prefix
    proxy_set_header   X-Real-IP $remote_addr;
    proxy_set_header   Host $host;
    proxy_read_timeout 30s;
}
```

Set in `.env`:
```
LICENSE_API_PUBLIC_URL=https://yourdomain.com/rejoin-api
LICENSE_API_PATH_PREFIX=
```

### nginx — prefix forwarded as-is

```nginx
location /rejoin-api/ {
    proxy_pass         http://127.0.0.1:8787;   # no trailing slash — prefix forwarded
    proxy_set_header   X-Real-IP $remote_addr;
}
```

Set in `.env`:
```
LICENSE_API_PUBLIC_URL=https://yourdomain.com/rejoin-api
LICENSE_API_PATH_PREFIX=/rejoin-api
```

### Caddy

```caddyfile
yourdomain.com {
    handle /rejoin-api/* {
        uri strip_prefix /rejoin-api
        reverse_proxy 127.0.0.1:8787
    }
}
```

Set in `.env`:
```
LICENSE_API_PUBLIC_URL=https://yourdomain.com/rejoin-api
LICENSE_API_PATH_PREFIX=
```

### Cloudflare Tunnel

Point the tunnel to `http://127.0.0.1:8787` with a public hostname subdomain (e.g. `api.yourdomain.com`), then:

```
LICENSE_API_PUBLIC_URL=https://api.yourdomain.com
LICENSE_API_PATH_PREFIX=
```

### Termux client usage

```sh
# 1. Check / bind license
curl -s -X POST https://yourdomain.com/rejoin-api/api/license/check \
  -H "Content-Type: application/json" \
  -d '{"key":"DENG-XXXX-XXXX-XXXX-XXXX","install_id_hash":"<sha256>","device_model":"Termux","app_version":"1.0.0"}'

# 2. Authorize download
curl -s -X POST https://yourdomain.com/rejoin-api/api/download/authorize \
  -H "Content-Type: application/json" \
  -d '{"key":"DENG-XXXX-XXXX-XXXX-XXXX","install_id_hash":"<sha256>","device_model":"Termux","app_version":"1.0.0","channel":"stable"}'

# 3. Download package (use download_url from step 2 response)
curl -L -o deng-tool-rejoin.zip "<download_url>"
```

### Health check

```sh
curl https://yourdomain.com/rejoin-api/api/license/health
# → {"status":"ok","version":"...","store":"supabase"}
```
