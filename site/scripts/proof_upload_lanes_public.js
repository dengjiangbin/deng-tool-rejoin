'use strict';

const https = require('https');

const URL = 'https://aio.deng.my.id/api/fishit-tracker/update-backpack';
const BUILD = 'UPLOAD_502_INTERVAL_SINGLETON_FIX_2026_06_15';
const RAW = 'https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua';

function post(body) {
  const payload = JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const req = https.request(URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
      timeout: 15000,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let json = null;
        try { json = JSON.parse(text); } catch { /* html */ }
        resolve({
          status: res.statusCode,
          route: res.headers['x-deng-tracker-route'] || null,
          body: json,
          html: text.includes('<html'),
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

async function main() {
  const counts = { '200': 0, '202': 0, '502': 0, '503': 0, other: 0, timeout: 0 };
  const bump = (s) => {
    const k = String(s);
    if (counts[k] != null) counts[k] += 1;
    else counts.other += 1;
  };

  const sample = {};
  for (const lane of [
    ['tracker_status', base('ProofStatusUser', { type: 'tracker_status' })],
    ['required_leaderstats', base('ProofLeaderUser', {
      uploadPath: 'playerdata_leaderstats_only',
      leaderstatsOnlyUpload: true,
      playerStats: { coins: 1, totalCaught: 1, source: 'leaderstats', build: BUILD },
    })],
    ['inventory_snapshot', base('ProofInvUser', {
      type: 'inventory_snapshot',
      inventorySource: 'playerdata_gameitemdb',
      fishItems: [{ itemId: '1', name: 'Clownfish', quantity: 1, source: 'playerdata_gameitemdb' }],
      playerStats: { coins: 1, totalCaught: 1, source: 'leaderstats', build: BUILD },
    })],
  ]) {
    const res = await post(lane[1]);
    bump(res.status);
    sample[lane[0]] = { status: res.status, route: res.route, html: res.html };
  }

  const users = 100;
  const batch = 20;
  let accepted = 0;
  let coalesced = 0;
  for (let start = 0; start < users; start += batch) {
    const tasks = [];
    for (let i = start; i < Math.min(users, start + batch); i += 1) {
      const u = `LoadUser${i}`;
      tasks.push(post(base(u, { type: 'tracker_status' })).then((r) => {
        bump(r.status);
        if (r.status === 200 || r.status === 202) accepted += 1;
        if (r.body?.coalesced) coalesced += 1;
      }).catch(() => { counts.timeout += 1; }));
      tasks.push(post(base(u, {
        uploadPath: 'playerdata_leaderstats_only',
        leaderstatsOnlyUpload: true,
        playerStats: { coins: i, totalCaught: i, source: 'leaderstats', build: BUILD },
      })).then((r) => {
        bump(r.status);
        if (r.status === 200 || r.status === 202) accepted += 1;
        if (r.body?.coalesced) coalesced += 1;
      }).catch(() => { counts.timeout += 1; }));
    }
    await Promise.all(tasks);
  }

  console.log(JSON.stringify({ sample, loadTest: { users, requests: users * 2, accepted, coalesced, counts } }, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
