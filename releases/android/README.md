# DENG All In One — Android APK Release Directory

Served by the website at:

* `https://aio.deng.my.id/download` — download landing page (login required)
* `https://aio.deng.my.id/downloads/deng-all-in-one-apk-latest.apk` — canonical latest alias
* `https://aio.deng.my.id/downloads/deng-all-in-one-apk-vX.Y.Z.apk` — versioned APK

Legacy aliases (`deng-tool-rejoin-apk-*`, `deng-monitor-*`) 301-redirect to the
canonical names above.

## Publishing

From repo root (requires release signing in `~/.gradle/gradle.properties`):

```powershell
powershell -File scripts/publish_apk_release.ps1
```

Then commit `latest.json` and the versioned `.apk`, deploy the site, and restart
PM2 if needed. Download routes set `Cache-Control: no-store` — no service worker
caches APK binaries.

## Manifest schema (`latest.json`)

```json
{
  "app_name": "DENG All In One",
  "version_name": "2.2.0",
  "version_code": 17,
  "file_name": "deng-all-in-one-apk-v2.2.0.apk",
  "sha256": "<lowercase hex>",
  "size_bytes": 1234567,
  "released_at": "2026-06-14T...",
  "build_marker": "APK_SYSTEM_BROWSER_DISCORD_AUTH_AIO_2026_06_14",
  "min_sdk": 26,
  "changelog": ["..."]
}
```

`file_name` must match `^deng-all-in-one(?:-apk)?-v?[A-Za-z0-9._-]+\.apk$`.
