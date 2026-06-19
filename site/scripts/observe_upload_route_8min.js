'use strict';

// 8-minute live public-route observation. Every cycle it POSTs all three lanes
// to the PUBLIC Cloudflare endpoint and GETs the public read API, counting any
// 502/503/530/HTML gateway response. Proves the upload route stays healthy with
// real traffic flowing (real Roblox accounts upload concurrently to the same
// ingest during this window).

const https = require('https');

const UPLOAD = 'https://aio.deng.my.id/api/fishit-tracker/update-backpack';
const READ = (u) => `https://aio.deng.my.id/api/tracker/get-backpack/${u}`;
const BUILD = 'UPLOAD_HTML_530_GATEWAY_DIAG_2026_06_15';
const RAW = 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';
const CYCLES = 9;          // ~9 cycles x 55s ≈ 8.25 min
const GAP_MS = 55_000;

function req(method, url, body) {
  return new Promise((resolve) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = https.request(url, {
      method,
      headers: payload
        ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) }
        : {},
      timeout: 20000,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        resolve({ status: res.statusCode, html: /<!DOCTYPE html>|<html/i.test(text),
          online: res.headers['x-deng-is-online'], age: res.headers['x-deng-status-age'],
          idsrc: res.headers['x-deng-report-identity-source'] });
      });
    });
    r.on('error', () => resolve({ status: 'ERR', html: false }));
    r.on('timeout', () => { r.destroy(); resolve({ status: 'TIMEOUT', html: false }); });
    if (payload) r.write(payload);
    r.end();
  });
}

function base(username, extra) {
  return { username, userId: 900000 + username.length, trackerBuild: BUILD,
    trackerChannel: 'fish-it-main', scriptSource: RAW, clientOrigin: 'roblox_tracker',
    evidenceSourceMode: 'live_roblox', isOnline: true, intervalSeconds: 60, ...extra };
}

async function main() {
  const totals = { status: {}, leaderstats: {}, inventory: {}, gatewayHtml: 0, c502: 0, c503: 0, c530: 0 };
  const bump = (lane, s) => { totals[lane][s] = (totals[lane][s] || 0) + 1;
    if (s === 502) totals.c502++; if (s === 503) totals.c503++; if (s === 530) totals.c530++; };
  const rows = [];
  for (let i = 1; i <= CYCLES; i += 1) {
    const t = new Date().toISOString().slice(11, 19);
    const s = await req('POST', UPLOAD, base('ObsStatus', { type: 'tracker_status' }));
    const l = await req('POST', UPLOAD, base('ObsLeader', { uploadPath: 'playerdata_leaderstats_only',
      leaderstatsOnlyUpload: true, playerStats: { coins: i, totalCaught: i, source: 'leaderstats', build: BUILD } }));
    const v = await req('POST', UPLOAD, base('ObsInv', { type: 'inventory_snapshot', inventorySource: 'playerdata_gameitemdb',
      fishItems: [{ itemId: '1', name: 'Clownfish', quantity: i, source: 'playerdata_gameitemdb' }],
      playerStats: { coins: i, totalCaught: i, source: 'leaderstats', build: BUILD } }));
    const rd = await req('GET', READ('usaxikan03'));
    bump('status', s.status); bump('leaderstats', l.status); bump('inventory', v.status);
    if (s.html || l.html || v.html) totals.gatewayHtml += 1;
    const row = `cycle ${i}/${CYCLES} ${t} | status=${s.status} leaderstats=${l.status} inventory=${v.status} | read=${rd.status} online=${rd.online} statusAge=${rd.age}s idsrc=${rd.idsrc} | html=${s.html || l.html || v.html}`;
    rows.push(row);
    console.log(row);
    if (i < CYCLES) await new Promise((r) => setTimeout(r, GAP_MS));
  }
  console.log('\n=== 8-MIN OBSERVATION SUMMARY ===');
  console.log(JSON.stringify(totals, null, 2));
  console.log(`TOTAL gateway-HTML=${totals.gatewayHtml} 502=${totals.c502} 503=${totals.c503} 530=${totals.c530}`);
}

main();
