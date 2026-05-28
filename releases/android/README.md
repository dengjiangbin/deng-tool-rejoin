# DENG Tool: Rejoin APK — Release Directory

This folder is served by the DENG Tool website at:

* `https://tool.deng.my.id/download` — public download landing page
* `https://tool.deng.my.id/downloads/<file>.apk` — direct APK download
* `https://tool.deng.my.id/downloads/deng-tool-rejoin-apk-latest.apk` — alias
  that resolves to whatever `latest.json` points to

Legacy aliases kept for backward compatibility (permanent redirect to the
canonical URL above):

* `https://tool.deng.my.id/downloads/deng-monitor-latest.apk`
* `https://tool.deng.my.id/downloads/deng-monitor-vX.Y.Z.apk`

## What this APK is

The **DENG Tool: Rejoin APK** is the **Android monitoring companion** for
DENG Tool: Rejoin. It is **not** a separate product, and its release version
is **not** tied to any Rejoin package version (stable / main-dev / test).

* Rejoin package versions are selected through the Discord/website package
  version system (e.g. `Select Version`).
* The APK has its own Android release version (currently `v1.0.0`).
* The APK is read-only — Rejoin packages still run in Termux as usual.

## Publishing a new build

1. Build a signed release APK from the `android/` project:

   ```bash
   cd android
   ./gradlew clean assembleRelease
   ```

   Output ends up at:
   `android/app/build/outputs/apk/release/app-release.apk`

2. Copy + rename it into this folder using the canonical version-stamped name:

   ```bash
   cp android/app/build/outputs/apk/release/app-release.apk \
      releases/android/deng-tool-rejoin-apk-v1.0.0.apk
   ```

3. Compute the SHA-256 and file size:

   ```bash
   sha256sum releases/android/deng-tool-rejoin-apk-v1.0.0.apk
   stat -c %s releases/android/deng-tool-rejoin-apk-v1.0.0.apk
   ```

4. Update `latest.json` with the new `file_name`, `sha256`, `size_bytes`,
   `version_name`, `version_code`, `released_at` (ISO date) and `changelog`.

5. Commit + deploy the site. The download page automatically picks up the
   new manifest — no code change needed.

## Manifest schema (`latest.json`)

```json
{
  "version_name": "1.0.0",
  "version_code": 1,
  "file_name": "deng-tool-rejoin-apk-v1.0.0.apk",
  "sha256": "<lowercase hex SHA-256>",
  "size_bytes": 4123456,
  "released_at": "2026-05-28",
  "min_sdk": 26,
  "changelog": [
    "Line 1",
    "Line 2"
  ]
}
```

`file_name` must match `^deng-tool-rejoin-apk-v?[A-Za-z0-9._-]+\.apk$` —
enforced by the website route to defend against path traversal. Legacy
`^deng-monitor-v?[A-Za-z0-9._-]+\.apk$` filenames are still accepted but
will 301-redirect to the new pattern when no on-disk file matches.

## What lives here

* `latest.json` — manifest used to render the website download page and
  resolve the `/downloads/deng-tool-rejoin-apk-latest.apk` alias.
* `deng-tool-rejoin-apk-vX.Y.Z.apk` — versioned signed APKs (do not commit
  if huge; see `.gitignore`).
* `README.md` — this file.
