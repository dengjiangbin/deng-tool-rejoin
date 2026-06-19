'use strict';

// Paced public upload proof — mimics real Roblox client cadence (sequential lanes,
// gaps between cycles) instead of a self-inflicted instantaneous burst. Proves the
// public Cloudflare -> ingest path returns 200/202 JSON (never HTML 502/503) for all
// three lanes from outside localhost.

const https = require('https');

const URL = 'https://aio.deng.my.id/api/fishit-tracker/update-backpack';
const BUILD = process.env.PROOF_TRACKER_BUILD || 'UPLOAD_HTML_530_GATEWAY_DIAG_2026_06_15';
const RAW = 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';
const CYCLES = Number(process.env.PROOF_CYCLES || 6);
const GAP_MS = Number(process.env.PROOF_GAP_MS || 3000);

function post(body) {
  const payload = JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const req = https.request(URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
      timeout: 30000,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let json = null;
        try { json = JSON.parse(text); } catch { /* html or text */ }
        resolve({
          status: res.statusCode,
          ms: Date.now() - started,
          route: res.headers['x-deng-tracker-route'] || null,
          html: /<!doctype html|<html/i.test(text),
          bodyPreview: json ? 'json' : text.slice(0, 40).replace(/\s+/g, ' '),
        });
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    req.write(payload);
    req.end();
  });
}

function base(username, extra = {}) {
  return {
    username,
    userId: 900000 + username.length,
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

function lanePayloads(suffix) {
  return [
    ['required_status', base(`PacedStatus${suffix}`, { type: 'tracker_status' })],
    ['required_leaderstats', base(`PacedLeader${suffix}`, {
      uploadPath: 'playerdata_leaderstats_only',
      leaderstatsOnlyUpload: true,
      playerStats: { coins: 12345, totalCaught: 678, source: 'leaderstats', build: BUILD },
    })],
    ['inventory_snapshot', base(`PacedInv${suffix}`, {
      type: 'inventory_snapshot',
      inventorySource: 'playerdata_gameitemdb',
      fishItems: [{ itemId: '1', name: 'Clownfish', quantity: 3, source: 'playerdata_gameitemdb' }],
      playerStats: { coins: 12345, totalCaught: 678, source: 'leaderstats', build: BUILD },
    })],
  ];
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const rows = [];
  const counts = { ok: 0, bad: 0, html: 0, '502': 0, '503': 0, '530': 0 };
  const laneOk = { required_status: 0, required_leaderstats: 0, inventory_snapshot: 0 };
  for (let c = 0; c < CYCLES; c += 1) {
    for (const [lane, payload] of lanePayloads(`${c}`)) {
      let r;
      try {
        r = await post(payload);
      } catch (e) {
        r = { status: 'ERR', ms: -1, html: false, bodyPreview: e.message };
      }
      const ok = r.status === 200 || r.status === 202;
      if (ok) { counts.ok += 1; laneOk[lane] += 1; } else { counts.bad += 1; }
      if (r.html) counts.html += 1;
      if (counts[String(r.status)] != null) counts[String(r.status)] += 1;
      rows.push({ cycle: c, lane, status: r.status, ms: r.ms, route: r.route, html: r.html, body: r.bodyPreview });
      console.log(`cycle=${c} lane=${lane} status=${r.status} ms=${r.ms} html=${r.html} body=${r.bodyPreview}`);
      await sleep(250);
    }
    if (c < CYCLES - 1) await sleep(GAP_MS);
  }
  console.log('\n=== SUMMARY ===');
  console.log(JSON.stringify({ cycles: CYCLES, counts, laneOk, totalRequests: rows.length }, null, 2));
  const clean = counts['502'] === 0 && counts['503'] === 0 && counts['530'] === 0 && counts.html === 0 && counts.bad === 0;
  console.log(clean ? 'RESULT=PASS (all lanes 200/202 JSON, 0x502/503/530/HTML)' : 'RESULT=FAIL');
  process.exit(clean ? 0 : 2);
}

main().catch((e) => { console.error(e); process.exit(1); });
