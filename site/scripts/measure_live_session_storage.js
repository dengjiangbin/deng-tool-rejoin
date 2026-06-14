'use strict';

const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const legacyPath = path.join(root, 'data', 'fishit_live_sessions.json');
const shardedRoot = path.join(root, 'data', 'fishit_live_sessions');

function measureLegacy() {
  if (!fs.existsSync(legacyPath)) return null;
  const st = fs.statSync(legacyPath);
  const raw = JSON.parse(fs.readFileSync(legacyPath, 'utf8'));
  const keys = Object.keys(raw.sessions || {}).filter((k) => !k.startsWith('uid:'));
  let total = 0;
  let max = 0;
  let maxKey = '';
  for (const k of keys) {
    const n = Buffer.byteLength(JSON.stringify(raw.sessions[k]), 'utf8');
    total += n;
    if (n > max) {
      max = n;
      maxKey = k;
    }
  }
  return {
    mode: 'legacy_monolith',
    fileBytes: st.size,
    accountCount: keys.length,
    avgAccountBytes: keys.length ? Math.round(total / keys.length) : 0,
    maxAccountBytes: max,
    maxAccountKey: maxKey,
  };
}

function measureSharded() {
  const indexPath = path.join(shardedRoot, 'index.json');
  if (!fs.existsSync(indexPath)) return null;
  const index = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
  const keys = Object.keys(index.accounts || {});
  let total = 0;
  let max = 0;
  let maxKey = '';
  for (const k of keys) {
    const file = path.join(shardedRoot, 'accounts', `${k.replace(/[^a-z0-9_-]/g, '_')}.json`);
    if (!fs.existsSync(file)) continue;
    const n = fs.statSync(file).size;
    total += n;
    if (n > max) {
      max = n;
      maxKey = k;
    }
  }
  const indexBytes = fs.statSync(indexPath).size;
  return {
    mode: 'sharded_current_state',
    fileBytes: total + indexBytes,
    indexBytes,
    accountCount: keys.length,
    avgAccountBytes: keys.length ? Math.round(total / keys.length) : 0,
    maxAccountBytes: max,
    maxAccountKey: maxKey,
  };
}

const report = {
  capturedAt: new Date().toISOString(),
  legacy: measureLegacy(),
  sharded: measureSharded(),
};

console.log(JSON.stringify(report, null, 2));

const out = path.join(root, 'proofs', 'live_session_storage_measurements.json');
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(report, null, 2));
