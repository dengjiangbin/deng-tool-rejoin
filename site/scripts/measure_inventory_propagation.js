'use strict';

// Real upload->display latency for the new lane.
// `lastInventoryAt` is stamped by the ingest server at the moment an inventory
// upload is accepted. This probe watches the live shard files for a NEW
// lastInventoryAt value (= a real inventory upload just happened) and measures
// how long until the 8793 read API serves a snapshot carrying that same value.
// Latency = (time 8793 reflects it) - (server upload timestamp). No fake data.

const http = require('http');
const fs = require('fs');
const path = require('path');

const ACCOUNTS_DIR = path.join(
  process.env.FISHIT_LIVE_SESSIONS_DIR || path.join(__dirname, '..', 'data', 'fishit_live_sessions'),
  'accounts',
);
const DURATION_MS = Number(process.argv[2] || 60000);
const POLL_MS = 400;

function readShardInventoryAt(user) {
  try {
    const j = JSON.parse(fs.readFileSync(path.join(ACCOUNTS_DIR, `${user}.json`), 'utf8'));
    const s = j.session || j;
    return s.lastInventoryAt || null;
  } catch (_) { return null; }
}

function read8793(user) {
  return new Promise((resolve) => {
    http.get(`http://127.0.0.1:8793/api/tracker/get-backpack/${encodeURIComponent(user)}?lite=1&_=${Date.now()}`, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        let inv = null;
        try { inv = JSON.parse(Buffer.concat(chunks).toString('utf8')).lastInventoryAt || null; } catch (_) { inv = null; }
        resolve(inv);
      });
    }).on('error', () => resolve(null));
  });
}

function activeUsers(limit) {
  return fs.readdirSync(ACCOUNTS_DIR)
    .filter((f) => f.endsWith('.json'))
    .map((f) => ({ u: f.replace(/\.json$/, ''), m: fs.statSync(path.join(ACCOUNTS_DIR, f)).mtimeMs }))
    .sort((a, b) => b.m - a.m)
    .slice(0, limit)
    .map((x) => x.u);
}

async function main() {
  const users = activeUsers(Number(process.env.PROP_USERS || 120));
  const lastKnownInv = new Map();
  const pending = new Map(); // user -> { invAt, detectedMs }
  const latencies = [];
  const start = Date.now();

  // Seed current state.
  for (const u of users) lastKnownInv.set(u, readShardInventoryAt(u));

  while (Date.now() - start < DURATION_MS) {
    for (const u of users) {
      const cur = readShardInventoryAt(u);
      const prev = lastKnownInv.get(u);
      if (cur && cur !== prev) {
        lastKnownInv.set(u, cur);
        if (!pending.has(u)) pending.set(u, { invAt: cur, detectedMs: Date.now() });
      }
    }
    // Check pending for catch-up on 8793.
    const pendingUsers = [...pending.keys()];
    // eslint-disable-next-line no-await-in-loop
    await Promise.all(pendingUsers.map(async (u) => {
      const served = await read8793(u);
      const p = pending.get(u);
      if (served && p && served >= p.invAt) {
        const uploadMs = Date.parse(p.invAt);
        const lat = Date.now() - uploadMs;
        if (Number.isFinite(lat) && lat >= 0 && lat < 120000) latencies.push(lat);
        pending.delete(u);
      }
    }));
    // eslint-disable-next-line no-await-in-loop
    await new Promise((r) => setTimeout(r, POLL_MS));
  }

  latencies.sort((a, b) => a - b);
  const pct = (p) => (latencies.length ? latencies[Math.min(latencies.length - 1, Math.ceil((p / 100) * latencies.length) - 1)] : null);
  const summary = {
    durationMs: DURATION_MS,
    usersWatched: users.length,
    inventoryUploadsObserved: latencies.length,
    uploadToDisplayMs: latencies.length ? {
      min: latencies[0], p50: pct(50), p95: pct(95), max: latencies[latencies.length - 1],
    } : null,
  };
  console.log(JSON.stringify(summary, null, 2));
  const outPath = path.join(__dirname, '..', 'proofs', 'upload_to_display_proof.json');
  fs.writeFileSync(outPath, JSON.stringify({ capturedAt: new Date().toISOString(), summary, latencies }, null, 2));
  console.log('written', outPath);
}

main().catch((e) => { console.error(e); process.exit(1); });
