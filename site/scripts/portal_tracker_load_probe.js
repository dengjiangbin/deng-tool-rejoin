'use strict';

/**
 * Load test: hammer tracker read on 8793 while polling portal /license on 8790.
 * Usage: node scripts/portal_tracker_load_probe.js [rounds]
 */
const ROUNDS = Number(process.argv[2] || 20);

async function one(url, opts = {}) {
  const started = Date.now();
  try {
    const res = await fetch(url, { ...opts, signal: AbortSignal.timeout(8000) });
    return { url, status: res.status, ms: Date.now() - started, ok: res.status < 400 };
  } catch (err) {
    return { url, status: 'ERR', ms: Date.now() - started, ok: false, err: err.message };
  }
}

async function main() {
  const results = [];
  const t0 = Date.now();
  for (let i = 0; i < ROUNDS; i += 1) {
    const batch = await Promise.all([
      one('http://127.0.0.1:8790/license', { redirect: 'manual' }),
      one('http://127.0.0.1:8790/healthz'),
      one('http://127.0.0.1:8793/healthz'),
      one('http://127.0.0.1:8791/tracker', { redirect: 'manual' }),
      one('http://127.0.0.1:8793/api/tracker/get-backpack/denghub2?lite=1'),
    ]);
    results.push(...batch);
  }
  const license = results.filter((r) => r.url.includes(':8790/license'));
  const licenseOk = license.filter((r) => r.ok || r.status === 302);
  const license502 = license.filter((r) => r.status === 502);
  const licenseP95 = license.map((r) => r.ms).sort((a, b) => a - b);
  const p95 = licenseP95[Math.min(licenseP95.length - 1, Math.floor(licenseP95.length * 0.95))] || 0;
  const summary = {
    at: new Date().toISOString(),
    rounds: ROUNDS,
    wallMs: Date.now() - t0,
    licenseTotal: license.length,
    licenseOk: licenseOk.length,
    license502: license502.length,
    licenseP95Ms: p95,
    all502: results.filter((r) => r.status === 502).length,
  };
  console.log(JSON.stringify(summary, null, 2));
  if (license502.length || licenseOk.length !== license.length) process.exit(1);
}

main();
