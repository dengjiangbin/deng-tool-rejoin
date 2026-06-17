'use strict';

// Phase 5 image-cache proof: confirm precomputed snapshots carry OWNED local
// image URLs (not hotlinks), the on-disk cache is populated, and the read lane
// never re-resolves/re-downloads per request (it serves the precomputed body).

const http = require('http');
const fs = require('fs');
const path = require('path');

// Avoid require()-ing the image cache module here: it pulls in heavy catalog
// init. We only need the on-disk cache dir + index.json, which we read directly.
const CACHE_DIR = process.env.FISHIT_FISH_IMAGE_CACHE_DIR
  || path.join(__dirname, '..', 'data', 'fish_image_cache');
const INDEX_PATH = path.join(CACHE_DIR, 'index.json');

function readIndexStats() {
  try {
    const raw = JSON.parse(fs.readFileSync(INDEX_PATH, 'utf8'));
    const rows = Object.values(raw.byAssetId || {});
    const cached = rows.filter((r) => r.cached === true || r.imageStatus === 'cached');
    return {
      cachedCount: cached.length,
      missingCount: rows.length - cached.length,
      byAssetIdEntries: rows.length,
      byNameEntries: Object.keys(raw.byName || {}).length,
      byUrlEntries: Object.keys(raw.byUrl || {}).length,
      byLocalFileEntries: Object.keys(raw.byLocalFile || {}).length,
      indexUpdatedAt: raw.updatedAt || null,
    };
  } catch (e) {
    return { error: e.message };
  }
}

function get(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      let s = '';
      res.on('data', (d) => { s += d; });
      res.on('end', () => resolve({ status: res.statusCode, body: s, headers: res.headers }));
    });
    req.on('error', () => resolve({ status: 0, body: '', headers: {} }));
    req.setTimeout(5000, () => { req.destroy(); resolve({ status: -1, body: '', headers: {} }); });
  });
}

(async () => {
  const users = ['denghub2', 'kingree030'];
  const perUser = [];
  for (const u of users) {
    // eslint-disable-next-line no-await-in-loop
    const r = await get(`http://127.0.0.1:8793/api/tracker/get-backpack/${u}?lite=1`);
    if (r.status !== 200) { perUser.push({ user: u, status: r.status }); continue; }
    let body;
    try { body = JSON.parse(r.body); } catch (_) { perUser.push({ user: u, parseError: true }); continue; }
    const rows = [];
    for (const arr of [body.fishItems, body.stoneItems, body.totemItems]) {
      if (Array.isArray(arr)) rows.push(...arr);
    }
    let local = 0; let external = 0; let none = 0;
    for (const it of rows) {
      const url = it.imageUrl || '';
      if (!url) none += 1;
      else if (url.startsWith('/api/fishit-tracker/assets/')) local += 1;
      else external += 1;
    }
    perUser.push({
      user: u,
      status: 200,
      readMode: r.headers['x-deng-read-mode'],
      precomputedAgeMs: r.headers['x-deng-precomputed-age-ms'],
      rows: rows.length,
      localImageUrls: local,
      externalImageUrls: external,
      noImage: none,
    });
  }

  // On-disk cache stats.
  const dir = CACHE_DIR;
  let fileCount = 0;
  let dirBytes = 0;
  try {
    for (const f of fs.readdirSync(dir)) {
      if (f === 'index.json' || f.endsWith('.tmp')) continue;
      const st = fs.statSync(path.join(dir, f));
      fileCount += 1; dirBytes += st.size;
    }
  } catch (_) { /* ignore */ }

  const out = {
    capturedAt: new Date().toISOString(),
    cacheDir: dir,
    indexStats: readIndexStats(),
    onDiskFiles: fileCount,
    onDiskMB: +(dirBytes / 1048576).toFixed(1),
    dedupeRule: 'sha256(first16).<ext>; index byAssetId/byName/byUrl/byLocalFile; skip download if localFile exists',
    perUser,
  };
  console.log(JSON.stringify(out, null, 2));
  fs.writeFileSync(path.join(__dirname, '..', 'proofs', 'image_cache_proof.json'), JSON.stringify(out, null, 2));
})();
