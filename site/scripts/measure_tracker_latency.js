'use strict';

// Reusable latency probe for the tracker lanes. No fake data — it hits real
// endpoints and records real wall-clock timing + HTTP status codes.
//
// Usage:
//   node scripts/measure_tracker_latency.js --base http://127.0.0.1:8791 \
//     --users denghub2,kakaoomol2 --reads 30 --label baseline_8791_local
//
// Output: JSON proof file under site/proofs/<label>.json plus a console summary.

const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

function percentile(sortedMs, p) {
  if (!sortedMs.length) return null;
  const idx = Math.min(sortedMs.length - 1, Math.max(0, Math.ceil((p / 100) * sortedMs.length) - 1));
  return sortedMs[idx];
}

function timeRequest(targetUrl, extraHeaders) {
  return new Promise((resolve) => {
    const lib = targetUrl.startsWith('https:') ? https : http;
    const startedAt = process.hrtime.bigint();
    const req = lib.get(targetUrl, { headers: { 'Cache-Control': 'no-cache', ...extraHeaders } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const ms = Number(process.hrtime.bigint() - startedAt) / 1e6;
        resolve({
          ok: true,
          status: res.statusCode,
          ms,
          bytes: Buffer.concat(chunks).length,
          headers: res.headers,
        });
      });
    });
    req.on('error', (err) => {
      const ms = Number(process.hrtime.bigint() - startedAt) / 1e6;
      resolve({ ok: false, status: 0, ms, error: err.message });
    });
    req.setTimeout(60000, () => {
      req.destroy(new Error('timeout'));
    });
  });
}

async function run() {
  const args = parseArgs(process.argv);
  const base = args.base || 'http://127.0.0.1:8791';
  const users = String(args.users || 'denghub2').split(',').map((s) => s.trim()).filter(Boolean);
  const reads = parseInt(args.reads || '30', 10);
  const label = args.label || `tracker_latency_${Date.now()}`;
  const lite = args.lite === undefined ? '1' : String(args.lite);
  const includeHome = args.home === '1' || args.home === true;

  const samples = { getBackpack: [], tracker: [], home: [] };
  const statusCounts = {};
  const headerProof = {};

  function recordStatus(group, status) {
    const k = `${group}:${status}`;
    statusCounts[k] = (statusCounts[k] || 0) + 1;
  }

  for (let i = 0; i < reads; i += 1) {
    const user = users[i % users.length];
    const url = `${base}/api/tracker/get-backpack/${encodeURIComponent(user)}?lite=${lite}&_=${Date.now()}_${i}`;
    // eslint-disable-next-line no-await-in-loop
    const r = await timeRequest(url);
    samples.getBackpack.push(r.ms);
    recordStatus('get-backpack', r.status);
    if (r.headers && !headerProof.getBackpack) {
      headerProof.getBackpack = {
        'x-deng-tracker-read-route': r.headers['x-deng-tracker-read-route'] || null,
        'x-deng-precomputed': r.headers['x-deng-precomputed'] || null,
        'x-deng-read-mode': r.headers['x-deng-read-mode'] || null,
        'x-deng-read-fallback': r.headers['x-deng-read-fallback'] || null,
        'x-deng-served-by': r.headers['x-deng-served-by'] || null,
      };
    }
  }

  if (args['tracker-page'] === '1') {
    for (let i = 0; i < reads; i += 1) {
      const url = `${base}/tracker?_=${Date.now()}_${i}`;
      // eslint-disable-next-line no-await-in-loop
      const r = await timeRequest(url);
      samples.tracker.push(r.ms);
      recordStatus('tracker', r.status);
    }
  }

  if (includeHome) {
    for (let i = 0; i < reads; i += 1) {
      const url = `${base}/?_=${Date.now()}_${i}`;
      // eslint-disable-next-line no-await-in-loop
      const r = await timeRequest(url);
      samples.home.push(r.ms);
      recordStatus('home', r.status);
    }
  }

  function stats(arr) {
    if (!arr.length) return null;
    const sorted = [...arr].sort((a, b) => a - b);
    return {
      count: sorted.length,
      min: Math.round(sorted[0]),
      p50: Math.round(percentile(sorted, 50)),
      p95: Math.round(percentile(sorted, 95)),
      max: Math.round(sorted[sorted.length - 1]),
      avg: Math.round(sorted.reduce((a, b) => a + b, 0) / sorted.length),
    };
  }

  const proof = {
    label,
    capturedAt: new Date().toISOString(),
    base,
    users,
    reads,
    lite,
    summary: {
      getBackpack: stats(samples.getBackpack),
      tracker: stats(samples.tracker),
      home: stats(samples.home),
    },
    statusCounts,
    headerProof,
  };

  const proofDir = path.join(__dirname, '..', 'proofs');
  fs.mkdirSync(proofDir, { recursive: true });
  const outPath = path.join(proofDir, `${label}.json`);
  fs.writeFileSync(outPath, JSON.stringify(proof, null, 2));

  console.log(JSON.stringify(proof.summary, null, 2));
  console.log('statusCounts', JSON.stringify(statusCounts));
  console.log('headerProof', JSON.stringify(headerProof));
  console.log('written', outPath);
}

run().catch((err) => {
  console.error('measure failed', err);
  process.exit(1);
});
