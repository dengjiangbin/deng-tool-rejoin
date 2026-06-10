#!/usr/bin/env node
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

const OUT = path.join(__dirname, '..', 'proofs', 'blocker10zt6_interval_proof.json');
const USER = process.argv[2] || 'denghub2';
const WAIT_MS = Number(process.argv[3] || 10000);

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(body) }); }
        catch (e) { reject(e); }
      });
    });
    req.setTimeout(8000, () => req.destroy(new Error('timeout')));
    req.on('error', reject);
  });
}

function pickStats(data) {
  const ps = data && data.playerStats;
  return {
    coinsText: ps && ps.coinsText,
    totalCaughtText: ps && ps.totalCaughtText,
    rarestFishChance: ps && ps.rarestFishChance,
    fishCount: Array.isArray(data && data.fishItems) ? data.fishItems.length : 0,
    lastInventoryAt: data && data.lastInventoryAt,
    connectionLive: data && data.connectionLive,
  };
}

async function main() {
  const base = `http://127.0.0.1:8791/api/fishit-tracker/get-backpack/${encodeURIComponent(USER)}`;
  const first = await fetchJson(base);
  await new Promise((r) => setTimeout(r, WAIT_MS));
  const second = await fetchJson(base);
  const proof = {
    user: USER,
    waitMs: WAIT_MS,
    firstAt: new Date().toISOString(),
    secondAt: new Date().toISOString(),
    first: pickStats(first.body),
    second: pickStats(second.body),
    statsPayloadPresentBothPolls: !!(first.body.playerStats && second.body.playerStats),
    fishPresentBothPolls: pickStats(first.body).fishCount > 0 && pickStats(second.body).fishCount > 0,
  };
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(proof, null, 2), 'utf8');
  console.log('BLOCKER10ZT6_INTERVAL_PROOF', JSON.stringify(proof));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
