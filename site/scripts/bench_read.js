'use strict';
// Quick local latency probe for the 8793 read lane. Removes PowerShell IWR
// overhead from the measurement. Usage: node scripts/bench_read.js [user] [n] [concurrency] [port]
const http = require('http');

const user = process.argv[2] || 'denghub2';
const n = parseInt(process.argv[3] || '60', 10);
const concurrency = parseInt(process.argv[4] || '1', 10);
const port = parseInt(process.argv[5] || '8793', 10);

function once(i) {
  return new Promise((resolve) => {
    const t0 = process.hrtime.bigint();
    const req = http.request({
      host: '127.0.0.1', port, method: 'GET',
      path: `/api/tracker/get-backpack/${user}?lite=1&_=${i}_${Math.random()}`,
    }, (res) => {
      let bytes = 0;
      res.on('data', (c) => { bytes += c.length; });
      res.on('end', () => {
        const ms = Number(process.hrtime.bigint() - t0) / 1e6;
        resolve({ ms, code: res.statusCode, bytes });
      });
    });
    req.on('error', () => resolve({ ms: -1, code: -1, bytes: 0 }));
    req.end();
  });
}

(async () => {
  const results = [];
  let idx = 0;
  async function lane() {
    while (idx < n) { const i = idx++; results.push(await once(i)); }
  }
  const t0 = Date.now();
  await Promise.all(Array.from({ length: concurrency }, lane));
  const wall = Date.now() - t0;
  const ms = results.map((r) => r.ms).sort((a, b) => a - b);
  const p = (q) => ms[Math.min(ms.length - 1, Math.floor(ms.length * q))];
  const codes = results.reduce((m, r) => { m[r.code] = (m[r.code] || 0) + 1; return m; }, {});
  console.log(`user=${user} n=${n} conc=${concurrency} port=${port}`);
  console.log(`  p50=${p(0.5).toFixed(1)}ms p95=${p(0.95).toFixed(1)}ms p99=${p(0.99).toFixed(1)}ms min=${ms[0].toFixed(1)} max=${ms[ms.length - 1].toFixed(1)}`);
  console.log(`  bytes=${results[0].bytes} wallMs=${wall} rps=${(n / (wall / 1000)).toFixed(0)} codes=${JSON.stringify(codes)}`);
})();
