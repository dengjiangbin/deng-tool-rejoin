'use strict';

// Part 11 live validation: poll the read API authoritative presence contract for
// both accounts every 30s for ~11 minutes. Asserts presence matches real age
// (online iff statusAge < 150s), ages advance with real time (never reset), the
// conditional fetch stays tiny on unchanged snapshots, and no 5xx ever occurs.

const http = require('http');
const fs = require('fs');

const USERS = ['denghub2', 'dengjiangbin'];
const ONLINE = 150;
const ITERATIONS = 23; // ~11.5 min at 30s
const out = [];
const hashes = {};
let prevAge = {};

function get(path) {
  return new Promise((resolve, reject) => {
    const s = Date.now();
    const r = http.get('http://127.0.0.1:8793' + path, (x) => {
      let n = 0;
      x.on('data', (c) => { n += c.length; });
      x.on('end', () => resolve({ h: x.headers, bytes: n, ms: Date.now() - s, status: x.statusCode }));
    });
    r.on('error', reject);
  });
}

let anomalies = 0;
async function tick(i) {
  const row = { t: new Date().toISOString(), i };
  for (const u of USERS) {
    const full = await get('/api/tracker/get-backpack/' + u + '?_=' + Date.now());
    const hash = full.h['x-deng-snapshot-hash'];
    const cond = await get('/api/tracker/get-backpack/' + u + '?h=' + hash + '&_=' + Date.now());
    const ps = full.h['x-deng-presence-state'];
    const age = Number(full.h['x-deng-status-age']);
    const online = full.h['x-deng-is-online'] === '1';
    // Invariant 1: presence must agree with the real age window.
    const expectOnline = Number.isFinite(age) && age < ONLINE;
    const presenceOk = online === expectOnline;
    // Invariant 2: age never resets backwards while hash is stable.
    let ageOk = true;
    if (hashes[u] === hash && prevAge[u] != null && Number.isFinite(age)) {
      ageOk = age >= prevAge[u] - 2; // allow tiny clock jitter
    }
    // Invariant 3: conditional fetch tiny when unchanged.
    const condOk = cond.h['x-deng-unchanged'] === '1' ? cond.bytes < 4000 : true;
    // Invariant 4: no 5xx.
    const httpOk = full.status === 200 && cond.status === 200;
    if (!presenceOk || !ageOk || !condOk || !httpOk) anomalies += 1;
    row[u] = { ps, age, online, presenceOk, ageOk, condOk, httpOk, fullB: full.bytes, condB: cond.bytes, condUnchanged: cond.h['x-deng-unchanged'] };
    hashes[u] = hash;
    if (Number.isFinite(age)) prevAge[u] = age;
  }
  out.push(row);
  // eslint-disable-next-line no-console
  console.log(`[${i + 1}/${ITERATIONS}] ` + USERS.map((u) => `${u}:${row[u].ps}/${row[u].age}s online=${row[u].online} cond=${row[u].condB}B(${row[u].condUnchanged})`).join('  '));
  fs.writeFileSync('proofs/live_presence_validation_2026_06_18.json', JSON.stringify({ anomalies, rows: out }, null, 2));
}

(async () => {
  for (let i = 0; i < ITERATIONS; i += 1) {
    try { await tick(i); } catch (e) { console.log('ERR', e.message); anomalies += 1; }
    if (i < ITERATIONS - 1) await new Promise((r) => setTimeout(r, 30000));
  }
  console.log('DONE anomalies=' + anomalies);
  fs.writeFileSync('proofs/live_presence_validation_2026_06_18.json', JSON.stringify({ anomalies, done: true, rows: out }, null, 2));
})();
