'use strict';

/**
 * Staged burst load probe for the tracker ingest path.
 *
 * Usage:
 *   node scripts/burst_load_probe.js [direct|public] [sizes=10,25,50,100]
 *
 *   direct -> http://127.0.0.1:8792 (origin only, no Cloudflare)
 *   public -> https://aio.deng.my.id (full Cloudflare path)
 *
 * Measures per-stage: p50/p95/p99 latency, status counts, 502/503/timeout/html,
 * plus origin event-loop lag and queue depth sampled from /metrics.
 */

const http = require('http');
const https = require('https');

const MODE = (process.argv[2] || 'direct').toLowerCase();
const SIZES = (process.argv[3] || '10,25,50,100').split(',').map((n) => parseInt(n, 10)).filter(Boolean);

const DIRECT_BASE = process.env.PROBE_DIRECT_BASE || 'http://127.0.0.1:8792';
const PUBLIC_BASE = process.env.PROBE_PUBLIC_BASE || 'https://aio.deng.my.id';
const BASE = MODE === 'public' ? PUBLIC_BASE : DIRECT_BASE;
const UPLOAD_PATH = '/api/fishit-tracker/update-backpack';
const METRICS_URL = `${DIRECT_BASE}/metrics`;
const BUILD = process.env.PROBE_BUILD || 'UPLOAD_DEBUG_OFF_NO_SYNC_DEBUG_2026_06_15';
const RAW = 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';
const REQ_TIMEOUT_MS = parseInt(process.env.PROBE_TIMEOUT_MS || '20000', 10);

const agentHttp = new http.Agent({ keepAlive: true, maxSockets: 256 });
const agentHttps = new https.Agent({ keepAlive: true, maxSockets: 256 });

function request(urlStr, body) {
  const u = new URL(urlStr);
  const isHttps = u.protocol === 'https:';
  const lib = isHttps ? https : http;
  const payload = body ? JSON.stringify(body) : null;
  const started = process.hrtime.bigint();
  return new Promise((resolve) => {
    const req = lib.request(u, {
      method: body ? 'POST' : 'GET',
      agent: isHttps ? agentHttps : agentHttp,
      headers: body ? {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      } : {},
      timeout: REQ_TIMEOUT_MS,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const ms = Number(process.hrtime.bigint() - started) / 1e6;
        const text = Buffer.concat(chunks).toString('utf8');
        let json = null;
        try { json = JSON.parse(text); } catch { /* html/text */ }
        resolve({
          status: res.statusCode,
          ms,
          route: res.headers['x-deng-tracker-route'] || null,
          json,
          html: /<html|<!DOCTYPE/i.test(text),
          coalesced: json && json.coalesced === true,
        });
      });
    });
    req.on('error', () => {
      const ms = Number(process.hrtime.bigint() - started) / 1e6;
      resolve({ status: 'error', ms, route: null, json: null, html: false });
    });
    req.on('timeout', () => {
      req.destroy();
      const ms = Number(process.hrtime.bigint() - started) / 1e6;
      resolve({ status: 'timeout', ms, route: null, json: null, html: false });
    });
    if (payload) req.write(payload);
    req.end();
  });
}

function bigFishItems(n) {
  const out = [];
  for (let i = 0; i < n; i += 1) {
    out.push({
      itemId: String(1000 + i),
      name: `Fish_${i}`,
      quantity: (i % 7) + 1,
      rarity: ['common', 'uncommon', 'rare', 'epic', 'legendary'][i % 5],
      source: 'playerdata_gameitemdb',
      weight: (i * 1.37) % 50,
      uuid: `uuid-${i}-${Math.random().toString(36).slice(2, 10)}`,
    });
  }
  return out;
}

function base(username, extra = {}) {
  return {
    username,
    userId: 900000 + (username.length * 31),
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

function makeLanePayload(lane, u, i) {
  if (lane === 'status') return base(u, { type: 'tracker_status' });
  if (lane === 'leaderstats') {
    return base(u, {
      uploadPath: 'playerdata_leaderstats_only',
      leaderstatsOnlyUpload: true,
      playerStats: { coins: i * 10, totalCaught: i, source: 'leaderstats', build: BUILD },
    });
  }
  if (lane === 'debug') {
    return base(u, {
      type: 'inventory_snapshot',
      debugUpload: true,
      uploadMode: 'debug',
      inventorySource: 'playerdata_gameitemdb',
      fishItems: bigFishItems(40),
    });
  }
  // inventory — realistic large snapshot
  return base(u, {
    type: 'inventory_snapshot',
    inventorySource: 'playerdata_gameitemdb',
    fishItems: bigFishItems(120),
    stoneItems: bigFishItems(20).map((x) => ({ ...x, source: 'playerdata_gameitemdb', kind: 'stone' })),
    playerStats: { coins: i * 100, totalCaught: i * 3, source: 'leaderstats', build: BUILD },
  });
}

function pct(sorted, p) {
  if (!sorted.length) return 0;
  const idx = Math.min(sorted.length - 1, Math.floor(sorted.length * p));
  return Math.round(sorted[idx]);
}

async function sampleMetrics() {
  const r = await request(METRICS_URL, null);
  if (!r.json) return null;
  return {
    lagMs: r.json.eventLoop?.lagMs ?? r.json.queue?.eventLoopLagMs ?? null,
    lagMax: r.json.eventLoop?.lagMax ?? null,
    queued: r.json.queue?.queued ?? null,
    active: r.json.queue?.active ?? null,
    shed: r.json.queue?.shedEvents ?? null,
    dropped: r.json.queue?.droppedJobs ?? null,
  };
}

async function runStage(size) {
  const lanes = ['status', 'leaderstats', 'inventory', 'debug'];
  const tasks = [];
  const results = [];
  let maxLag = 0;
  let maxQueue = 0;
  const metricsTimer = setInterval(async () => {
    const m = await sampleMetrics();
    if (m && typeof m.lagMs === 'number') maxLag = Math.max(maxLag, m.lagMs);
    if (m && typeof m.queued === 'number') maxQueue = Math.max(maxQueue, m.queued);
  }, 150);

  const startWall = Date.now();
  for (let i = 0; i < size; i += 1) {
    const u = `Burst${MODE}_${i}`;
    // Each simulated client fires its 4 lanes in parallel (worst case burst).
    for (const lane of lanes) {
      tasks.push(request(BASE + UPLOAD_PATH, makeLanePayload(lane, u, i)).then((r) => {
        results.push({ lane, ...r });
      }));
    }
  }
  await Promise.all(tasks);
  const wallMs = Date.now() - startWall;
  clearInterval(metricsTimer);
  const finalMetrics = await sampleMetrics();
  if (finalMetrics && typeof finalMetrics.lagMs === 'number') maxLag = Math.max(maxLag, finalMetrics.lagMs);

  const summary = { size, requests: results.length, wallMs, byLane: {}, counts: {}, maxLag, maxQueue };
  const allMs = [];
  for (const r of results) {
    allMs.push(r.ms);
    const sc = String(r.status);
    summary.counts[sc] = (summary.counts[sc] || 0) + 1;
    if (r.html) summary.counts.html = (summary.counts.html || 0) + 1;
    if (!summary.byLane[r.lane]) summary.byLane[r.lane] = { ms: [], status: {} };
    summary.byLane[r.lane].ms.push(r.ms);
    summary.byLane[r.lane].status[sc] = (summary.byLane[r.lane].status[sc] || 0) + 1;
  }
  allMs.sort((a, b) => a - b);
  summary.latency = { p50: pct(allMs, 0.5), p95: pct(allMs, 0.95), p99: pct(allMs, 0.99), max: Math.round(allMs[allMs.length - 1] || 0) };
  for (const lane of Object.keys(summary.byLane)) {
    const arr = summary.byLane[lane].ms.sort((a, b) => a - b);
    summary.byLane[lane].latency = { p50: pct(arr, 0.5), p95: pct(arr, 0.95), p99: pct(arr, 0.99) };
    delete summary.byLane[lane].ms;
  }
  return summary;
}

async function main() {
  console.log(`# burst_load_probe mode=${MODE} base=${BASE} sizes=${SIZES.join(',')}`);
  const before = await sampleMetrics();
  console.log('metrics_before', JSON.stringify(before));
  const stages = [];
  for (const size of SIZES) {
    const s = await runStage(size);
    stages.push(s);
    console.log(`\n=== stage ${size} clients (${s.requests} reqs in ${s.wallMs}ms) ===`);
    console.log('latency_all', JSON.stringify(s.latency));
    console.log('counts', JSON.stringify(s.counts));
    console.log('maxLag', s.maxLag, 'maxQueue', s.maxQueue);
    for (const lane of Object.keys(s.byLane)) {
      console.log(`  ${lane}`, JSON.stringify(s.byLane[lane].status), JSON.stringify(s.byLane[lane].latency));
    }
    await new Promise((r) => setTimeout(r, 3000)); // settle between stages
  }
  console.log('\n# SUMMARY');
  for (const s of stages) {
    const c = s.counts;
    const bad = (c['502'] || 0) + (c['503'] || 0) + (c.timeout || 0) + (c.error || 0) + (c.html || 0);
    console.log(`size=${s.size} p50=${s.latency.p50} p95=${s.latency.p95} p99=${s.latency.p99} ` +
      `200=${c['200'] || 0} 202=${c['202'] || 0} 4xx=${(c['400'] || 0) + (c['403'] || 0) + (c['429'] || 0)} ` +
      `502=${c['502'] || 0} 503=${c['503'] || 0} timeout=${c.timeout || 0} err=${c.error || 0} html=${c.html || 0} ` +
      `maxLag=${s.maxLag} maxQueue=${s.maxQueue} BAD=${bad}`);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
