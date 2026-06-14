'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const { execSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const LEGACY = path.join(ROOT, 'data', 'fishit_live_sessions.json');
const SHARDED = path.join(ROOT, 'data', 'fishit_live_sessions');

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: 15000 }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

function countTmpFiles(dir) {
  let count = 0;
  if (!fs.existsSync(dir)) return 0;
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) count += countTmpFiles(p);
    else if (ent.name.endsWith('.tmp')) count += 1;
  }
  return count;
}

function portOwner(port) {
  try {
    const out = execSync(`netstat -ano | findstr ":${port} " | findstr LISTENING`, { encoding: 'utf8' });
    const line = out.split(/\r?\n/).find((l) => l.includes('LISTENING'));
    if (!line) return null;
    const parts = line.trim().split(/\s+/);
    return Number(parts[parts.length - 1]) || null;
  } catch {
    return null;
  }
}

function pm2Pid(name) {
  try {
    const out = execSync(`pm2 jlist`, { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
    const list = JSON.parse(out);
    const proc = list.find((p) => p.name === name);
    return proc && proc.pid ? proc.pid : null;
  } catch {
    return null;
  }
}

function gitHead() {
  try {
    return execSync('git rev-parse HEAD', { cwd: path.join(ROOT, '..'), encoding: 'utf8' }).trim();
  } catch {
    return null;
  }
}

(async () => {
  const legacyStat = fs.existsSync(LEGACY) ? fs.statSync(LEGACY) : null;
  const indexPath = path.join(SHARDED, 'index.json');
  const indexStat = fs.existsSync(indexPath) ? fs.statSync(indexPath) : null;
  const accountFiles = fs.existsSync(path.join(SHARDED, 'accounts'))
    ? fs.readdirSync(path.join(SHARDED, 'accounts')).filter((f) => f.endsWith('.json'))
    : [];

  const ingestHealth = await fetchJson('http://127.0.0.1:8792/health');
  const ingestMetrics = await fetchJson('http://127.0.0.1:8792/metrics');
  const siteHealth = await fetchJson('http://127.0.0.1:8791/health');

  const webPid = pm2Pid('deng-tool-site');
  const ingestPid = pm2Pid('deng-tracker-ingest');
  const port8791 = portOwner(8791);
  const port8792 = portOwner(8792);

  const proof = {
    marker: 'FRESH_START_BEFORE_60MIN_LIVE_PROOF',
    capturedAt: new Date().toISOString(),
    gitHead: gitHead(),
    queueDepth: ingestHealth.queue?.queued ?? ingestMetrics.queue?.queued ?? null,
    pendingFlushCount: ingestMetrics.sessionStore?.pendingAccountCount ?? 0,
    pendingDirty: ingestMetrics.sessionStore?.pendingDirty ?? false,
    deferredPending: ingestMetrics.queue?.deferredPending ?? 0,
    deferredQueued: ingestMetrics.queue?.deferredQueued ?? 0,
    deferredActive: ingestMetrics.queue?.deferredActive ?? 0,
    tmpFilesInData: countTmpFiles(path.join(ROOT, 'data')),
    legacyMonolith: legacyStat
      ? { bytes: legacyStat.size, mtime: legacyStat.mtime.toISOString() }
      : null,
    sharded: {
      active: ingestMetrics.sessionStore?.mode === 'sharded',
      accountFiles: accountFiles.length,
      indexBytes: indexStat ? indexStat.size : 0,
      totalBytes: ingestMetrics.sessionStore?.totalBytes ?? null,
    },
    pm2: {
      dengToolSite: { pid: webPid, port8791Owner: port8791, portMatch: webPid === port8791 },
      dengTrackerIngest: { pid: ingestPid, port8792Owner: port8792, portMatch: ingestPid === port8792 },
    },
    orphanProcessCount: [port8791, port8792].filter((p, i) => {
      const owner = i === 0 ? port8791 : port8792;
      const expected = i === 0 ? webPid : ingestPid;
      return owner && expected && owner !== expected;
    }).length,
    eventLoopLagMs: ingestMetrics.eventLoop?.lagMs ?? ingestHealth.eventLoop?.lagMs ?? null,
    memory: ingestMetrics.memory || null,
    services: { siteHealth, ingestHealthSummary: { status: ingestHealth.status, queue: ingestHealth.queue } },
  };

  const outPath = path.join(ROOT, 'proofs', 'fresh_start_before_60min_proof.json');
  fs.writeFileSync(outPath, `${JSON.stringify(proof, null, 2)}\n`);
  console.log(JSON.stringify(proof, null, 2));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
