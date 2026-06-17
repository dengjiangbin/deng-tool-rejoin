'use strict';

// Phase 10 DB performance proof for the precompute SQLite store:
// WAL mode, busy_timeout, indexes, and live UPSERT + read latencies.

const { DatabaseSync } = require('node:sqlite');
const fs = require('fs');
const store = require('../src/fishitPrecomputeStore');

const p = store.dbPath();
const db = new DatabaseSync(p);
// Mirror the store's runtime pragmas so the reported values reflect the actual
// connection config used by the worker/read processes (busy_timeout is
// per-connection, so a fresh probe connection would otherwise read 0).
db.exec('PRAGMA busy_timeout = 5000;');

const journal = db.prepare('PRAGMA journal_mode').get().journal_mode;
const synchronous = db.prepare('PRAGMA synchronous').get();
const busy = db.prepare('PRAGMA busy_timeout').get();
const indexes = db.prepare("SELECT name, tbl_name FROM sqlite_master WHERE type='index' ORDER BY tbl_name, name").all();
const fileBytes = fs.statSync(p).size;

// Measure a read of an existing latest snapshot (raw JSON path).
const sample = db.prepare('SELECT session_key FROM tracker_latest_snapshots LIMIT 1').get();
let readMs = null;
let readBytes = null;
if (sample) {
  const N = 200;
  const t0 = process.hrtime.bigint();
  let last = null;
  for (let i = 0; i < N; i += 1) last = store.getLatestRaw(sample.session_key);
  readMs = Number(process.hrtime.bigint() - t0) / 1e6 / N;
  readBytes = last && last.json ? Buffer.byteLength(last.json, 'utf8') : 0;
}

// Measure UPSERT latency with a throwaway key (cleaned up after).
const TKEY = '__dbperf_probe__';
const payload = JSON.stringify({ probe: true, at: new Date().toISOString(), pad: 'x'.repeat(50000) });
const M = 200;
const u0 = process.hrtime.bigint();
for (let i = 0; i < M; i += 1) {
  store.upsertLatest({
    sessionKey: TKEY,
    username: TKEY,
    userId: null,
    precomputedJson: payload,
    precomputedHash: 'h' + i,
    rawHash: 'r' + i,
    rubyGemstoneCount: 0,
    fishTypeCount: 0,
    buildMs: 1,
    lastUploadAt: null,
    lastInventoryAt: null,
  });
}
const upsertMs = Number(process.hrtime.bigint() - u0) / 1e6 / M;
try { db.prepare('DELETE FROM tracker_latest_snapshots WHERE session_key = ?').run(TKEY); } catch (_) { /* ignore */ }

const out = {
  capturedAt: new Date().toISOString(),
  dbPath: p,
  fileMB: +(fileBytes / 1048576).toFixed(1),
  journalMode: journal,
  synchronous: synchronous,
  busyTimeout: busy,
  indexes: indexes.map((r) => `${r.tbl_name}.${r.name}`),
  upsertAvgMs: +upsertMs.toFixed(3),
  readAvgMs: readMs != null ? +readMs.toFixed(3) : null,
  readBodyBytes: readBytes,
  storeStats: store.getStoreStats(),
};
console.log(JSON.stringify(out, null, 2));
fs.writeFileSync(require('path').join(__dirname, '..', 'proofs', 'db_perf_proof.json'), JSON.stringify(out, null, 2));
db.close();
