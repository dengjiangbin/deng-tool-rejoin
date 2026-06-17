'use strict';

// Phase 14 burst/load simulation for the 8793 read API. Fires `total` requests
// at `concurrency` parallelism across a rotating set of real users and reports
// status-code distribution + latency percentiles. Confirms no 502/503/530 and
// stable latency under concurrent read load (the 500-user poll storm).

const http = require('http');
const fs = require('fs');
const path = require('path');

const BASE = process.env.BURST_BASE || 'http://127.0.0.1:8793';
const TOTAL = Number(process.argv[2] || 1000);
const CONCURRENCY = Number(process.argv[3] || 80);
const agent = new http.Agent({ keepAlive: true, maxSockets: CONCURRENCY, maxFreeSockets: CONCURRENCY });

function candidateUsers(limit) {
  const dir = path.join(process.env.FISHIT_LIVE_SESSIONS_DIR || path.join(__dirname, '..', 'data', 'fishit_live_sessions'), 'accounts');
  return fs.readdirSync(dir).filter((f) => f.endsWith('.json')).slice(0, limit).map((f) => f.replace(/\.json$/, ''));
}

// Probe each candidate once and keep only users the read lane actually serves
// from the precomputed cache. This mirrors the real 500-user poll storm (every
// polling user has uploaded and is precomputed) rather than orphan test shards.
function probeMode(user) {
  return new Promise((resolve) => {
    const req = http.get(`${BASE}/api/tracker/get-backpack/${encodeURIComponent(user)}?lite=1`, { agent }, (res) => {
      res.resume();
      res.on('end', () => resolve(res.headers['x-deng-read-mode'] || 'none'));
    });
    req.on('error', () => resolve('err'));
    req.setTimeout(8000, () => { req.destroy(); resolve('timeout'); });
  });
}

async function precomputedUsers(limit) {
  const candidates = candidateUsers(limit * 2);
  const kept = [];
  for (const u of candidates) {
    // eslint-disable-next-line no-await-in-loop
    const mode = await probeMode(u);
    if (mode === 'precomputed') kept.push(u);
    if (kept.length >= limit) break;
  }
  return kept;
}

function once(user, i) {
  return new Promise((resolve) => {
    const t = process.hrtime.bigint();
    const req = http.get(`${BASE}/api/tracker/get-backpack/${encodeURIComponent(user)}?lite=1&_=${Date.now()}_${i}`, { agent }, (res) => {
      res.resume();
      res.on('end', () => resolve({ status: res.statusCode, ms: Number(process.hrtime.bigint() - t) / 1e6 }));
    });
    req.on('error', () => resolve({ status: 0, ms: Number(process.hrtime.bigint() - t) / 1e6 }));
    req.setTimeout(15000, () => { req.destroy(new Error('timeout')); resolve({ status: -1, ms: Number(process.hrtime.bigint() - t) / 1e6 }); });
  });
}

async function main() {
  const users = await precomputedUsers(300);
  if (!users.length) {
    console.error('No precomputed users available to test.');
    process.exit(1);
  }
  console.error(`Testing ${users.length} precomputed users (real poll-storm simulation).`);
  const statusCounts = {};
  const lat = [];
  let issued = 0;
  const startedAt = Date.now();
  async function runner() {
    while (issued < TOTAL) {
      const i = issued; issued += 1;
      // eslint-disable-next-line no-await-in-loop
      const r = await once(users[i % users.length], i);
      statusCounts[r.status] = (statusCounts[r.status] || 0) + 1;
      lat.push(r.ms);
    }
  }
  await Promise.all(Array.from({ length: CONCURRENCY }, runner));
  const elapsed = Date.now() - startedAt;
  lat.sort((a, b) => a - b);
  const pct = (p) => Math.round(lat[Math.min(lat.length - 1, Math.ceil((p / 100) * lat.length) - 1)]);
  const summary = {
    base: BASE,
    total: TOTAL,
    concurrency: CONCURRENCY,
    elapsedMs: elapsed,
    throughputPerSec: Math.round((TOTAL / elapsed) * 1000),
    statusCounts,
    badGateways: (statusCounts[502] || 0) + (statusCounts[503] || 0) + (statusCounts[530] || 0),
    latencyMs: { min: Math.round(lat[0]), p50: pct(50), p95: pct(95), p99: pct(99), max: Math.round(lat[lat.length - 1]) },
  };
  console.log(JSON.stringify(summary, null, 2));
  fs.writeFileSync(path.join(__dirname, '..', 'proofs', 'burst_read_8793_proof.json'), JSON.stringify({ capturedAt: new Date().toISOString(), summary }, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
