'use strict';

// Measures how fresh the precomputed snapshots are for currently-active users by
// reading the X-DENG-Precomputed-Age-Ms header from the 8793 read API. This is a
// real upload->display propagation proxy: a low age means the worker is keeping
// each user's snapshot current shortly after their uploads land on disk.

const http = require('http');
const fs = require('fs');
const path = require('path');

function head(user) {
  return new Promise((resolve) => {
    http.get(`http://127.0.0.1:8793/api/tracker/get-backpack/${encodeURIComponent(user)}?lite=1&_=${Date.now()}`, (res) => {
      res.resume();
      resolve({
        user,
        status: res.statusCode,
        precomputed: res.headers['x-deng-precomputed'],
        ageMs: res.headers['x-deng-precomputed-age-ms'] ? Number(res.headers['x-deng-precomputed-age-ms']) : null,
        fallback: res.headers['x-deng-read-fallback'],
      });
    }).on('error', () => resolve({ user, status: 0 }));
  });
}

async function main() {
  // Use the most recently active accounts from the shard directory.
  const dir = process.env.FISHIT_LIVE_SESSIONS_DIR
    || path.join(__dirname, '..', 'data', 'fishit_live_sessions');
  const accountsDir = path.join(dir, 'accounts');
  const files = fs.readdirSync(accountsDir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => ({ f, m: fs.statSync(path.join(accountsDir, f)).mtimeMs }))
    .sort((a, b) => b.m - a.m)
    .slice(0, Number(process.argv[2] || 50))
    .map((x) => x.f.replace(/\.json$/, ''));

  const results = [];
  for (const user of files) {
    // eslint-disable-next-line no-await-in-loop
    results.push(await head(user));
  }
  const ages = results.filter((r) => r.precomputed === '1' && r.ageMs != null).map((r) => r.ageMs).sort((a, b) => a - b);
  const pct = (p) => (ages.length ? ages[Math.min(ages.length - 1, Math.ceil((p / 100) * ages.length) - 1)] : null);
  const summary = {
    sampled: results.length,
    precomputedHits: ages.length,
    fallbacks: results.filter((r) => r.fallback === '1').length,
    notFound: results.filter((r) => r.status === 404).length,
    ageMs: ages.length ? { min: ages[0], p50: pct(50), p95: pct(95), max: ages[ages.length - 1] } : null,
  };
  console.log(JSON.stringify(summary, null, 2));
  const outPath = path.join(__dirname, '..', 'proofs', 'precompute_freshness_proof.json');
  fs.writeFileSync(outPath, JSON.stringify({ capturedAt: new Date().toISOString(), summary, results }, null, 2));
  console.log('written', outPath);
}

main().catch((e) => { console.error(e); process.exit(1); });
