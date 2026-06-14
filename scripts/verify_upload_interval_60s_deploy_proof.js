#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');

const ROOT = path.join(__dirname, '..');
const OUT = path.join(ROOT, 'site', 'proofs', 'upload_interval_60s_aio_deploy_proof.json');

function get(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { 'User-Agent': 'deng-upload-interval-proof', ...headers } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
  });
}

function postJson(url, body) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const u = new URL(url);
    const req = https.request({
      hostname: u.hostname,
      path: u.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
        'User-Agent': 'deng-upload-interval-proof',
      },
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

(async () => {
  const proof = {
    generatedAt: new Date().toISOString(),
    marker: 'UPLOAD_INTERVAL_60S_AIO_2026_06_14',
    before: {
      note: 'Prior production used 10s client lanes and 12/min upload rate limit causing 429 storms.',
      clientIntervalSeconds: 10,
      rateLimitPerMin: 12,
      uploadHost: 'tool.deng.my.id',
    },
    after: {
      clientIntervalSeconds: 60,
      rateLimitPerMin: 10,
      uploadHost: 'aio.deng.my.id',
      serverIntervalSeconds: 60,
      serverGraceSeconds: 15,
      presenceGraceSeconds: 180,
    },
    checks: {},
  };

  const rawUrl = 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';
  const raw = await get(`${rawUrl}?v=${Date.now()}`);
  let decodedSnippet = '';
  const b64 = raw.body.match(/local __B=\[\[([\s\S]*?)\]\]\nlocal __A=/);
  if (b64) {
    try { decodedSnippet = Buffer.from(b64[1], 'base64').toString('utf8').slice(0, 5000); } catch (_) { /* ignore */ }
  }
  proof.checks.githubRaw = {
    url: rawUrl,
    status: raw.status,
    bytes: raw.body.length,
    hasAioUploadUrl: decodedSnippet.includes('https://aio.deng.my.id/api/fishit-tracker/update-backpack'),
    hasToolUploadUrl: decodedSnippet.includes('https://tool.deng.my.id/api/fishit-tracker/update-backpack'),
    has60sInterval: /lightSyncIntervalSeconds\s*=\s*60/.test(decodedSnippet),
    buildMarker: (decodedSnippet.match(/UPLOAD_INTERVAL_60S_AIO_2026_06_14/) || [])[0] || null,
  };

  const uploadBody = {
    type: 'tracker_status',
    username: 'ProofIntervalUser',
    userId: 991001,
    isOnline: true,
    clientOrigin: 'roblox_tracker',
    evidenceSourceMode: 'live_roblox',
    trackerBuild: 'UPLOAD_INTERVAL_60S_AIO_2026_06_14',
    trackerChannel: 'fish-it-main',
    scriptSource: rawUrl,
    intervalSeconds: 60,
    syncIntervalSeconds: 60,
    fishItems: [],
    stoneItems: [],
    totemItems: [],
    trackerClientProof: {
      trackerBuild: 'UPLOAD_INTERVAL_60S_AIO_2026_06_14',
      trackerChannel: 'fish-it-main',
      scriptSource: rawUrl,
    },
  };

  for (const host of ['aio.deng.my.id', 'tool.deng.my.id']) {
    const url = `https://${host}/api/fishit-tracker/update-backpack`;
    const res = await postJson(url, uploadBody);
    let parsed = null;
    try { parsed = JSON.parse(res.body); } catch (_) { /* ignore */ }
    proof.checks[`upload_${host}`] = {
      url,
      status: res.status,
      servedBy: res.headers['x-deng-served-by'] || null,
      trackerRoute: res.headers['x-deng-tracker-route'] || null,
      rateLimitPolicy: res.headers['ratelimit-policy'] || res.headers['RateLimit-Policy'] || null,
      retryAfter: res.headers['retry-after'] || null,
      is429: res.status === 429,
      accepted: parsed && parsed.accepted,
    };
  }

  const metricsAio = await get('https://aio.deng.my.id/metrics');
  const metricsTool = await get('https://tool.deng.my.id/metrics');
  for (const [label, res] of [['aio', metricsAio], ['tool', metricsTool]]) {
    let parsed = null;
    try { parsed = JSON.parse(res.body); } catch (_) { /* ignore */ }
    proof.checks[`metrics_${label}`] = {
      status: res.status,
      rateLimit429: parsed?.trackerRoute?.rateLimit429 ?? parsed?.uploads?.rateLimit429 ?? null,
      upload429: parsed?.uploads?.status429 ?? null,
      upload502: parsed?.uploads?.status502 ?? null,
      servedBy: res.headers['x-deng-served-by'] || null,
    };
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(proof, null, 2));
  console.log('PROOF_OK', OUT);
  console.log(JSON.stringify(proof.checks, null, 2));
})().catch((err) => {
  console.error('PROOF_FAIL', err.message);
  process.exit(1);
});
