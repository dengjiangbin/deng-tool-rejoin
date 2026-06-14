'use strict';

const INTERVAL_MS = 250;
let lagMs = 0;
let lagMax = 0;
const samples = [];

const timer = setInterval(() => {
  const start = process.hrtime.bigint();
  setImmediate(() => {
    const elapsed = Number(process.hrtime.bigint() - start) / 1e6;
    lagMs = Math.max(0, elapsed);
    lagMax = Math.max(lagMax, lagMs);
    samples.push(lagMs);
    if (samples.length > 240) samples.shift();
  });
}, INTERVAL_MS);
if (typeof timer.unref === 'function') timer.unref();

function percentile(sorted, pct) {
  if (!sorted.length) return 0;
  const idx = Math.min(sorted.length - 1, Math.floor(sorted.length * pct));
  return sorted[idx];
}

function getLagMs() {
  return lagMs;
}

function getMetrics() {
  const sorted = [...samples].sort((a, b) => a - b);
  return {
    lagMs,
    lagMax,
    p50: percentile(sorted, 0.5),
    p95: percentile(sorted, 0.95),
    sampleCount: samples.length,
  };
}

function _resetForTests() {
  lagMs = 0;
  lagMax = 0;
  samples.length = 0;
}

function _setLagForTests(ms) {
  lagMs = Number(ms) || 0;
}

module.exports = {
  getLagMs,
  getMetrics,
  _resetForTests,
  _setLagForTests,
};
