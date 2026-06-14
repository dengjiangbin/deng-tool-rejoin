'use strict';

/**
 * Poll ingest metrics + storage for 60 minutes after fresh start.
 * Records 502/503 counts, queue depth, lag, monolith growth, sharded bytes.
 */

const fs = require('fs');
const path = require('path');
const http = require('http');

const ROOT = path.join(__dirname, '..');
const LEGACY = path.join(ROOT, 'data', 'fishit_live_sessions.json');
const DURATION_MS = Number(process.env.LIVE_PROOF_DURATION_MS || 60 * 60 * 1000);
const INTERVAL_MS = Number(process.env.LIVE_PROOF_INTERVAL_MS || 60 * 1000);
const OUT = path.join(ROOT, 'proofs', 'live_60min_upload_proof.json');

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: 20000 }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(body) }); }
        catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

function legacyBytes() {
  try {
    return fs.existsSync(LEGACY) ? fs.statSync(LEGACY).size : 0;
  } catch {
    return null;
  }
}

async function sample(baseline502 = 0) {
  const metrics = await fetchJson('http://127.0.0.1:8792/metrics');
  const m = metrics.body || {};
  const uploads = m.uploads || {};
  let status502Total = 0;
  for (const row of Object.values(uploads.byRoute || {})) {
    status502Total += Number(row.status502) || 0;
  }
  return {
    at: new Date().toISOString(),
    queueDepth: m.queue?.queued ?? null,
    deferredActive: m.queue?.deferredActive ?? 0,
    pendingFlush: m.sessionStore?.pendingAccountCount ?? 0,
    eventLoopLagMs: m.eventLoop?.lagMs ?? null,
    heapUsedMb: m.memory ? Math.round(m.memory.heapUsed / 1024 / 1024) : null,
    status502Total,
    status502SinceBaseline: Math.max(0, status502Total - baseline502),
    upload503: m.trackerRoute?.hardFail503Count ?? 0,
    legacyMonolithBytes: legacyBytes(),
    shardedTotalBytes: m.sessionStore?.totalBytes ?? null,
    shardedMode: m.sessionStore?.mode ?? null,
  };
}

(async () => {
  const startedAt = new Date().toISOString();
  const samples = [];
  let errors = 0;
  let max502Delta = 0;
  let maxQueue = 0;
  let maxLag = 0;
  const end = Date.now() + DURATION_MS;

  let baseline502 = 0;
  try {
    const first = await sample(0);
    baseline502 = first.status502Total;
    samples.push(first);
    maxQueue = Number(first.queueDepth) || 0;
    maxLag = Number(first.eventLoopLagMs) || 0;
    console.log(`[live-proof] baseline status502=${baseline502} queue=${first.queueDepth} lag=${first.eventLoopLagMs}ms`);
  } catch (err) {
    console.warn('[live-proof] baseline sample failed:', err.message || err);
  }

  console.log(`[live-proof] starting ${DURATION_MS / 60000} minute monitor, interval ${INTERVAL_MS}ms`);

  while (Date.now() < end) {
    const wait = Math.min(INTERVAL_MS, end - Date.now());
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    try {
      const s = await sample(baseline502);
      samples.push(s);
      max502Delta = Math.max(max502Delta, Number(s.status502SinceBaseline) || 0);
      maxQueue = Math.max(maxQueue, Number(s.queueDepth) || 0);
      maxLag = Math.max(maxLag, Number(s.eventLoopLagMs) || 0);
      console.log(`[live-proof] ${s.at} queue=${s.queueDepth} lag=${s.eventLoopLagMs}ms 502+${s.status502SinceBaseline} legacy=${s.legacyMonolithBytes} sharded=${s.shardedTotalBytes}`);
    } catch (err) {
      errors += 1;
      console.warn('[live-proof] sample failed:', err.message || err);
      samples.push({ at: new Date().toISOString(), error: String(err.message || err) });
    }
  }

  const valid = samples.filter((s) => !s.error);
  const first = valid[0];
  const last = valid[valid.length - 1];
  const report = {
    marker: 'LIVE_60MIN_UPLOAD_PROOF',
    startedAt,
    finishedAt: new Date().toISOString(),
    durationMs: DURATION_MS,
    intervalMs: INTERVAL_MS,
    sampleCount: samples.length,
    sampleErrors: errors,
    status502Baseline: baseline502,
    max502DeltaDuringWindow: max502Delta,
    maxQueueDepth: maxQueue,
    maxEventLoopLagMs: maxLag,
    legacyMonolithGrowthBytes: (last && first)
      ? (last.legacyMonolithBytes - first.legacyMonolithBytes)
      : null,
    shardedBytesDelta: (last && first && last.shardedTotalBytes != null && first.shardedTotalBytes != null)
      ? (last.shardedTotalBytes - first.shardedTotalBytes)
      : null,
    passCriteria: {
      zero502DuringWindow: max502Delta === 0,
      queueBounded: maxQueue <= 50,
      legacyMonolithNotGrowing: (last && first)
        ? (last.legacyMonolithBytes === first.legacyMonolithBytes)
        : null,
      shardedModeActive: last?.shardedMode === 'sharded',
    },
    samples,
  };

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, `${JSON.stringify(report, null, 2)}\n`);
  console.log('[live-proof] complete:', JSON.stringify(report.passCriteria));
})();
