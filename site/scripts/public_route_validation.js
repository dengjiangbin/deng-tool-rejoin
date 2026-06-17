'use strict';

// Phase 13 public route validation through the Cloudflare tunnel.
// Confirms get-backpack/latest/snapshot hit the 8793 read lane, /tracker hits
// 8791, and a 100x repeated read produces NO 502/503/530.

const https = require('https');

const HOST = process.env.PUBLIC_HOST || 'aio.deng.my.id';
const USER = process.env.PUBLIC_USER || 'denghub2';

function req(pathname) {
  return new Promise((resolve) => {
    const started = process.hrtime.bigint();
    const r = https.get({ host: HOST, path: pathname, headers: { 'Cache-Control': 'no-store' } }, (res) => {
      let bytes = 0;
      res.on('data', (d) => { bytes += d.length; });
      res.on('end', () => resolve({
        status: res.statusCode,
        ms: Number(process.hrtime.bigint() - started) / 1e6,
        bytes,
        readRoute: res.headers['x-deng-tracker-read-route'] || null,
        readMode: res.headers['x-deng-read-mode'] || null,
        precomputed: res.headers['x-deng-precomputed'] || null,
        servedBy: res.headers['x-deng-served-by'] || null,
      }));
    });
    r.on('error', (e) => resolve({ status: 0, error: e.message }));
    r.setTimeout(15000, () => { r.destroy(); resolve({ status: -1, error: 'timeout' }); });
  });
}

(async () => {
  const ts = Date.now();
  const checks = {
    getBackpack: await req(`/api/tracker/get-backpack/${USER}?lite=1&_=${ts}`),
    latest: await req(`/api/tracker/latest/${USER}?_=${ts}`),
    snapshot: await req(`/api/tracker/snapshot/${USER}?_=${ts}`),
    trackerPage: await req(`/tracker?_=${ts}`),
    uploadRouteProbe: await req(`/api/fishit-tracker/health?_=${ts}`),
  };

  // 100x repeated read no-error test.
  const N = 100;
  const statusCounts = {};
  const lat = [];
  for (let i = 0; i < N; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    const r = await req(`/api/tracker/get-backpack/${USER}?lite=1&_=${ts}_${i}`);
    statusCounts[r.status] = (statusCounts[r.status] || 0) + 1;
    if (typeof r.ms === 'number') lat.push(r.ms);
  }
  lat.sort((a, b) => a - b);
  const pct = (p) => (lat.length ? Math.round(lat[Math.min(lat.length - 1, Math.ceil((p / 100) * lat.length) - 1)]) : null);

  const out = {
    capturedAt: new Date().toISOString(),
    host: HOST,
    user: USER,
    routeChecks: checks,
    repeatedRead: {
      count: N,
      statusCounts,
      badGateways: (statusCounts['502'] || 0) + (statusCounts['503'] || 0) + (statusCounts['530'] || 0),
      latencyMs: { p50: pct(50), p95: pct(95), p99: pct(99), max: lat.length ? Math.round(lat[lat.length - 1]) : null },
    },
  };
  console.log(JSON.stringify(out, null, 2));
  require('fs').writeFileSync(require('path').join(__dirname, '..', 'proofs', 'public_route_validation_proof.json'), JSON.stringify(out, null, 2));
})();
