'use strict';

// Phase 8 parallel-migration parity check: compare the legacy read path (8791)
// against the new precomputed read API (8793) for a set of usernames. Compares
// the visible dataset (fish/stone/totem rows, counts, leaderstats, status) with
// image-host normalization so the only differences flagged are real data drift.

const http = require('http');

function get(base, user) {
  return new Promise((resolve) => {
    const url = `${base}/api/tracker/get-backpack/${encodeURIComponent(user)}?lite=1&_=${Date.now()}`;
    http.get(url, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        let body = null;
        try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (_) { body = null; }
        resolve({ status: res.statusCode, body, headers: res.headers });
      });
    }).on('error', (err) => resolve({ status: 0, body: null, error: err.message }));
  });
}

function norm(v) {
  return String(v == null ? '' : v).trim().toLowerCase();
}
function stripHost(url) {
  return String(url == null ? '' : url).replace(/^https?:\/\/[^/]+/, '');
}
function rowFingerprint(row) {
  const insts = Array.isArray(row.ownedInstances) ? row.ownedInstances : [];
  return {
    name: norm(row.cleanName || row.baseFishName || row.name),
    mutation: norm(row.mutation || row.mutationName),
    amount: Math.max(0, Math.floor(Number(row.amount ?? row.count ?? row.quantity ?? 0))),
    instances: insts.length,
    image: stripHost(row.imageUrl || row.image || ''),
  };
}
function rowsFingerprint(rows) {
  return (Array.isArray(rows) ? rows : [])
    .map(rowFingerprint)
    .sort((a, b) => (a.name + a.mutation).localeCompare(b.name + b.mutation));
}
function statsFingerprint(body) {
  if (!body) return null;
  const ps = body.playerStats || {};
  return {
    fish: rowsFingerprint(body.fishItems),
    stones: rowsFingerprint(body.stoneItems),
    totems: rowsFingerprint(body.totemItems),
    coins: norm(ps.coins ?? ps.Coins),
    totalCaught: norm(ps.totalCaught ?? ps.TotalCaught),
    rarest: norm(ps.rarest ?? ps.rarestFish),
  };
}

function diffKeys(a, b) {
  const out = [];
  const sa = JSON.stringify(a);
  const sb = JSON.stringify(b);
  if (sa !== sb) {
    // Find which top-level group differs.
    for (const k of Object.keys(a || {})) {
      if (JSON.stringify(a[k]) !== JSON.stringify((b || {})[k])) out.push(k);
    }
  }
  return out;
}

async function main() {
  const oldBase = process.env.OLD_BASE || 'http://127.0.0.1:8791';
  const newBase = process.env.NEW_BASE || 'http://127.0.0.1:8793';
  const users = (process.argv[2] || 'denghub2').split(',').map((s) => s.trim()).filter(Boolean);
  const results = [];
  for (const user of users) {
    // eslint-disable-next-line no-await-in-loop
    const [oldR, newR] = await Promise.all([get(oldBase, user), get(newBase, user)]);
    const oldFp = statsFingerprint(oldR.body);
    const newFp = statsFingerprint(newR.body);
    const differingGroups = diffKeys(oldFp, newFp);
    results.push({
      user,
      oldStatus: oldR.status,
      newStatus: newR.status,
      newPrecomputed: newR.headers && newR.headers['x-deng-precomputed'],
      newFallback: newR.headers && newR.headers['x-deng-read-fallback'],
      oldFishTypes: oldFp ? oldFp.fish.length : null,
      newFishTypes: newFp ? newFp.fish.length : null,
      rubyOld: oldR.body && oldR.body.topCards && oldR.body.topCards.rubyGemstone ? oldR.body.topCards.rubyGemstone.count : 'n/a(old)',
      rubyNew: newR.body && newR.body.topCards && newR.body.topCards.rubyGemstone ? newR.body.topCards.rubyGemstone.count : null,
      match: oldR.status === newR.status && differingGroups.length === 0,
      differingGroups,
    });
  }
  console.log(JSON.stringify(results, null, 2));
  const mismatches = results.filter((r) => !r.match);
  console.log(`\nMATCH ${results.length - mismatches.length}/${results.length}`);
  if (mismatches.length) console.log('MISMATCH USERS:', mismatches.map((m) => `${m.user}[${m.differingGroups.join(',')}]`).join(', '));
}

main().catch((e) => { console.error(e); process.exit(1); });
