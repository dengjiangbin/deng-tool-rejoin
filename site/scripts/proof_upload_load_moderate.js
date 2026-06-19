'use strict';

// Moderate public upload load proof: 100 status-path uploads + 100 each of the other
// two lanes, at concurrency 5 (realistic, not a self-inflicted instantaneous DoS).
// Proves the public Cloudflare -> ingest path returns 200/202 JSON with 0x502/503/530
// and no HTML gateway body for all three lanes from outside localhost.

const https = require('https');

const URL = 'https://aio.deng.my.id/api/fishit-tracker/update-backpack';
const BUILD = process.env.PROOF_TRACKER_BUILD || 'UPLOAD_HTML_530_GATEWAY_DIAG_2026_06_15';
const RAW = 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';
const N = Number(process.env.PROOF_N || 100);
const CONCURRENCY = Number(process.env.PROOF_CONCURRENCY || 5);

function post(body) {
  const payload = JSON.stringify(body);
  return new Promise((resolve) => {
    const started = Date.now();
    const req = https.request(URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
      timeout: 30000,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let json = null; try { json = JSON.parse(text); } catch { /* non-json */ }
        resolve({ status: res.statusCode, ms: Date.now() - started, html: /<!doctype html|<html/i.test(text), json: !!json });
      });
    });
    req.on('error', () => resolve({ status: 'ERR', ms: Date.now() - started, html: false, json: false }));
    req.on('timeout', () => { req.destroy(); resolve({ status: 'TIMEOUT', ms: Date.now() - started, html: false, json: false }); });
    req.write(payload); req.end();
  });
}

function base(username, extra = {}) {
  return {
    username, userId: 900000 + username.length, trackerBuild: BUILD, trackerChannel: 'fish-it-main',
    scriptSource: RAW, clientOrigin: 'roblox_tracker', evidenceSourceMode: 'live_roblox', isOnline: true,
    intervalSeconds: 60, ...extra,
  };
}

function payloadFor(lane, i) {
  if (lane === 'required_status') return base(`LoadStatus${i}`, { type: 'tracker_status' });
  if (lane === 'required_leaderstats') return base(`LoadLeader${i}`, {
    uploadPath: 'playerdata_leaderstats_only', leaderstatsOnlyUpload: true,
    playerStats: { coins: i * 7, totalCaught: i, source: 'leaderstats', build: BUILD },
  });
  return base(`LoadInv${i}`, {
    type: 'inventory_snapshot', inventorySource: 'playerdata_gameitemdb',
    fishItems: [{ itemId: '1', name: 'Clownfish', quantity: i % 5, source: 'playerdata_gameitemdb' }],
    playerStats: { coins: i * 7, totalCaught: i, source: 'leaderstats', build: BUILD },
  });
}

async function runLane(lane) {
  const counts = { ok: 0, bad: 0, html: 0, '502': 0, '503': 0, '530': 0, ERR: 0, TIMEOUT: 0 };
  let maxMs = 0;
  let idx = 0;
  async function worker() {
    while (idx < N) {
      const i = idx++;
      const r = await post(payloadFor(lane, i));
      maxMs = Math.max(maxMs, r.ms);
      if (r.status === 200 || r.status === 202) counts.ok += 1; else counts.bad += 1;
      if (r.html) counts.html += 1;
      if (counts[String(r.status)] != null) counts[String(r.status)] += 1;
    }
  }
  await Promise.all(Array.from({ length: CONCURRENCY }, worker));
  return { lane, counts, maxMs };
}

async function main() {
  const results = [];
  for (const lane of ['required_status', 'required_leaderstats', 'inventory_snapshot']) {
    const r = await runLane(lane);
    results.push(r);
    console.log(`${lane}: ok=${r.counts.ok}/${N} 502=${r.counts['502']} 503=${r.counts['503']} 530=${r.counts['530']} html=${r.counts.html} err=${r.counts.ERR} timeout=${r.counts.TIMEOUT} maxMs=${r.maxMs}`);
  }
  const clean = results.every((r) => r.counts.ok === N && r.counts['502'] === 0 && r.counts['503'] === 0 && r.counts['530'] === 0 && r.counts.html === 0);
  console.log(JSON.stringify({ N, concurrency: CONCURRENCY, results }, null, 2));
  console.log(clean ? `RESULT=PASS (3 lanes x ${N} = ${N * 3}/${N * 3} 200/202 JSON, 0x502/503/530/HTML)` : 'RESULT=FAIL');
  process.exit(clean ? 0 : 2);
}

main();
