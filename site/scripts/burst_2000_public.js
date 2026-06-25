'use strict';

/**
 * 2000-user public burst: 6000 valid uploads (status + leaderstats + inventory).
 * Staged concurrency: 200 → 500 → 1000 → 2000.
 *
 * Usage: node scripts/burst_2000_public.js [baseUrl]
 */

const https = require('https');
const fs = require('fs');
const path = require('path');

const BASE = (process.argv[2] || 'https://aio.deng.my.id').replace(/\/+$/, '');
const PATH = '/api/fishit-tracker/update-backpack';
const USERS = 2000;
const BUILD = 'BURST_2000_PUBLIC_2026_06_25';
const RAW = 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';
const STAGES = [200, 500, 1000, 2000];
const SINGLE_PASS = process.env.BURST_SINGLE_PASS === '1';

const agent = new https.Agent({ keepAlive: true, maxSockets: 2048 });
const codes = {};
const routeHeaders = {};
const latencies = [];
let networkErrors = 0;

function bump(m, k) { m[k] = (m[k] || 0) + 1; }

function base(user, extra) {
  return {
    username: user,
    userId: 910000 + user.length * 17,
    trackerBuild: BUILD,
    trackerChannel: 'fish-it-main',
    scriptSource: RAW,
    clientOrigin: 'roblox_tracker',
    evidenceSourceMode: 'live_roblox',
    isOnline: true,
    intervalSeconds: 60,
    ...extra,
  };
}

function jobs() {
  const out = [];
  for (let i = 0; i < USERS; i += 1) {
    const user = `Burst2000_${i}`;
    out.push({ lane: 'status', body: base(user, { type: 'tracker_status', heartbeat: true }) });
    out.push({
      lane: 'leaderstats', body: base(user, {
        uploadPath: 'playerdata_leaderstats_only',
        leaderstatsOnlyUpload: true,
        playerStats: { coins: 1000 + i, totalCaught: 50 + (i % 20), source: 'leaderstats', build: BUILD },
      }),
    });
    out.push({
      lane: 'inventory', body: base(user, {
        type: 'inventory_snapshot',
        inventorySource: 'playerdata_gameitemdb',
        fishItems: [{ itemId: String(1000 + i), name: 'BurstFish', quantity: 1, rarity: 'common', source: 'playerdata_gameitemdb' }],
        playerStats: { coins: 1000 + i, totalCaught: 50, source: 'leaderstats', build: BUILD },
      }),
    });
  }
  return out;
}

function post(body) {
  const payload = JSON.stringify(body);
  const started = Date.now();
  return new Promise((resolve) => {
    const req = https.request(`${BASE}${PATH}`, {
      method: 'POST', agent,
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload), 'User-Agent': 'deng-burst-2000/1.0' },
      timeout: 20000,
    }, (res) => {
      res.resume();
      res.on('end', () => {
        latencies.push(Date.now() - started);
        bump(codes, String(res.statusCode));
        const route = res.headers['x-deng-tracker-route'] || res.headers['x-deng-ingest-route'] || res.headers['x-deng-served-by'] || '(none)';
        bump(routeHeaders, route);
        resolve(res.statusCode);
      });
    });
    req.on('error', () => { networkErrors += 1; bump(codes, 'NETERR'); resolve('NETERR'); });
    req.on('timeout', () => { req.destroy(); networkErrors += 1; bump(codes, 'TIMEOUT'); resolve('TIMEOUT'); });
    req.write(payload);
    req.end();
  });
}

async function runPool(allJobs, concurrency) {
  let idx = 0;
  async function worker() {
    while (idx < allJobs.length) {
      const j = allJobs[idx++];
      await post(j.body);
    }
  }
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
}

async function main() {
  const allJobs = jobs();
  const t0 = Date.now();
  if (SINGLE_PASS) {
    console.log(`[burst] single pass concurrency=2000 jobs=${allJobs.length}`);
    await runPool(allJobs, 2000);
  } else {
    for (const c of STAGES) {
      console.log(`[stage] concurrency=${c} jobs=${allJobs.length}`);
      await runPool(allJobs, c);
    }
  }
  latencies.sort((a, b) => a - b);
  const p = (q) => latencies.length ? latencies[Math.min(latencies.length - 1, Math.floor(latencies.length * q))] : 0;
  const attempts = SINGLE_PASS ? allJobs.length : allJobs.length * STAGES.length;
  const ok = (codes['200'] || 0) + (codes['202'] || 0);
  const report = {
    base: BASE + PATH, users: USERS, attempts, codes, routeHeaders, networkErrors, ok,
    bad502: codes['502'] || 0, bad503: codes['503'] || 0, bad530: codes['530'] || 0, bad429: codes['429'] || 0,
    latencyMs: { p50: p(0.5), p95: p(0.95), p99: p(0.99), max: latencies[latencies.length - 1] || 0 },
    wallMs: Date.now() - t0,
  };
  report.pass = report.bad502 === 0 && report.bad503 === 0 && report.bad530 === 0 && report.bad429 === 0 && networkErrors === 0 && ok === attempts;
  console.log(JSON.stringify(report, null, 2));
  fs.writeFileSync(path.join(__dirname, '..', 'proofs', 'burst_2000_public_proof.json'), JSON.stringify({ ...report, at: new Date().toISOString() }, null, 2));
  process.exit(report.pass ? 0 : 1);
}

main();
