# Upload Pipeline Known-Good Baseline (2026-06-14)

**Status:** LOCKED — do not regress without explicit review and new proof artifacts.

This document captures the verified-good Fish It tracker upload pipeline after the
2026-06-14 fixes. Future work (APK releases, UI polish, unrelated features) must
**not** break the behaviors listed here.

---

## Root causes fixed

| Issue | Symptom | Fix |
| --- | --- | --- |
| 10s client upload lanes + 12/min rate limit | 429 storms, stale indicators | 60s lanes, 10/min limit, aio upload host |
| HTTP 202 treated as failure in Roblox Lua | Uploads logged as failed despite server accept | `UPLOAD_HTTP_2XX_SUCCESS_FIX` — any 2xx/accepted body counts as success |
| Leaderstats fields dropped on session persist | Dashboard/tracker showed stale lane ages | `fishitSessionStore` roundtrips leaderstats upload fields |
| Ingest heartbeat deferred under load | `/tracker` read stale while ingest had fresh data | Immediate ingest flush (`FISHIT_SESSION_FLUSH_MS`) + sync to site session store |
| Untrusted `playerStats.build` on read path | Old builds blocked fresh stats display | Trust `UPLOAD_INTERVAL_60S_AIO` build marker in `fishitPlayerStats.js` |
| aio GET reads routed to ingest (8792) | 502/404 on `/api/fishit-tracker/get-backpack/*` | Read routes mirrored on `/api/tracker/*` via web (8791) |

---

## Fixes shipped (by layer)

### Roblox Lua (private source → public loader)

- **60s upload cadence:** `DEFAULT_UPLOAD_INTERVAL_SEC = 60`, three-lane stagger preserved.
- **aio upload URLs:** `https://aio.deng.my.id/api/fishit-tracker/update-backpack` and catalog URL on aio.
- **HTTP 2xx success:** `HttpDash.uploadOkFromResult` accepts 202 and other 2xx; no hard `statusCode == "200"` gate.
- **Build markers in decoded Lua:**
  - `UPLOAD_INTERVAL_60S_AIO_2026_06_14`
  - `UPLOAD_HTTP_2XX_SUCCESS_FIX_2026_06_14`

### Backend / session store / ingest

- `fishitLeaderstatsUpload.js` — lane timestamps, trusted build, derive status from server fields.
- `fishitSessionStore.js` — persist `leaderstatsUploadOk`, `leaderstatsUploadSeq`, `lastStatsUploadAt`, `lastSnapshotUploadAt`.
- `fishitTrackerRoutes.js` — `UPLOAD_INTERVAL_SECONDS = 60`, ingest sync flush, `/api/tracker/*` read mirror.
- `fishitPlayerStats.js` — trust list includes `UPLOAD_INTERVAL_60S_AIO`.
- `trackerUploadResponse.js` — 202 still promotes latest heartbeat state.

### Deploy / auto-update

- `dist/tracker.lua` built via `scripts/build_tracker_dist.js` (production build marker).
- Public loader URL unchanged: `https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua`
- PM2 asset version: `TOOL_SITE_ASSET_VERSION=UPLOAD_INTERVAL_60S_AIO_2026_06_14`
- `/tracker` auto-update serves new loader after users reload the public script.

---

## Commit IDs and markers

| Item | Value |
| --- | --- |
| Upload pipeline fix (HTTP 202 + ingest sync) | `8c9f4e7` |
| 60s lanes + aio domain + per-lane indicators | `bef1f85` |
| Restored AIO runtime modules (do not revert) | `289c242` |
| Public `tracker.lua` deploy (GitHub loader) | `d8166a1` *(remote deploy record)* |
| Tracker build marker | `UPLOAD_INTERVAL_60S_AIO_2026_06_14` |
| Lua HTTP fix marker | `UPLOAD_HTTP_2XX_SUCCESS_FIX_2026_06_14` |
| Proof artifact | `site/proofs/upload_interval_60s_aio_deploy_proof.json` |

**Preserve intact:** commits `289c242` and `bef1f85` must remain reachable; do not rewrite history over them.

---

## PM2 ports / process layout (from proof)

| Process | Port | Role |
| --- | --- | --- |
| `deng-tool-site` | **8791** | Web UI, OAuth, `/api/tracker/*` reads, upload proxy |
| `deng-tracker-ingest` | **8792** | Direct POST `/api/fishit-tracker/update-backpack` only |
| Control panel | 3099 | Internal (when running) |

From `site/proofs/cloudflare_direct_ingest_proof.json` (representative):

- `web8791`: pid varies by restart; service `deng-tool-site`, port 8791.
- `ingest8792`: service `deng-tracker-ingest`, port 8792.

From `site/proofs/upload_interval_60s_aio_deploy_proof.json`:

- `upload_aio.deng.my.id`: served by `deng-tracker-ingest`, `trackerRoute=direct-ingest`, rate limit `12;w=60`, not 429 under proof load.
- GitHub raw loader: 200, build marker present, aio URL present, tool URL absent.

---

## Route health proof (expected)

| URL | Expected |
| --- | --- |
| `https://aio.deng.my.id/` | 200 homepage |
| `https://aio.deng.my.id/tracker` | 200 (login redirect if anonymous) |
| `https://aio.deng.my.id/api/fishit-tracker/update-backpack` (POST) | 403 without auth / accepted with valid tracker payload |
| `https://aio.deng.my.id/api/tracker/get-backpack/{user}` | 200 with hydrated session data |
| `https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua` | 200, marker `UPLOAD_INTERVAL_60S_AIO_2026_06_14` |

Re-run proof:

```powershell
node scripts/verify_upload_interval_60s_deploy_proof.js
node site/tests/tracker_upload_interval_60s_regression.test.js
node site/tests/tracker_upload_pipeline_202_success.test.js
```

---

## Expected Roblox console behavior (after users reload public loader)

1. `[TRACKER] build=UPLOAD_INTERVAL_60S_AIO_2026_06_14` (or equivalent build log).
2. Upload interval ~60s per lane (status / inventory / leaderstats staggered).
3. On HTTP **202** or other 2xx: `REQUIRED_LEADERSTATS_UPLOAD_OK` / success logs — **not** treated as failure.
4. `playerdata_direct` / dashboard snapshot success lines without false `http_202` failure classification.
5. `/tracker` web UI shows fresh lane ages (green/yellow/red) aligned with last successful upload timestamps.

Users must **re-execute** the public loader (`loadstring(game:HttpGet(...))`) once to pick up the fixed script. Do **not** introduce new public loader URLs.

---

## Regression warning

**Do not change without replacement proof:**

- 60-second upload cadence (client + server `UPLOAD_INTERVAL_SECONDS`).
- HTTP 202 / 2xx success handling in Lua and `finishTrackerUploadResponse`.
- Trusted leaderstats build list (`UPLOAD_INTERVAL_60S_AIO`).
- Ingest → site session sync flush behavior.
- Public loader URL and `/tracker` auto-update path.
- aio.deng.my.id as canonical upload host for new clients.

APK, download page, and OAuth work must stay in separate commits/files and must not modify tracker upload logic except this documentation.

---

## Related tests (must stay green)

- `site/tests/tracker_upload_interval_60s_regression.test.js`
- `site/tests/tracker_upload_pipeline_202_success.test.js`
- `site/tests/fishit_three_separate_indicators.test.js`
- `site/tests/fishit_pcall_required_upload.test.js`
