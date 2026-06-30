'use strict';

const { buildStabilityStatus } = require('./stabilityStatus');

const INTERVAL_MS = Number(process.env.STABILITY_SNAPSHOT_INTERVAL_MS || 30_000);
let cachedSnapshot = null;
let cachedJson = null;
let cachedAt = null;
let refreshTimer = null;

function refreshStabilitySnapshot() {
  try {
    cachedSnapshot = buildStabilityStatus();
    cachedAt = new Date().toISOString();
    cachedSnapshot.snapshotCachedAt = cachedAt;
    cachedSnapshot.snapshotSource = 'precomputed';
    cachedJson = JSON.stringify(cachedSnapshot);
  } catch (err) {
    console.warn('[stability-snapshot] refresh failed:', err.message || err);
  }
}

function startStabilitySnapshotLoop() {
  if (refreshTimer) return;
  refreshStabilitySnapshot();
  refreshTimer = setInterval(refreshStabilitySnapshot, INTERVAL_MS);
  if (typeof refreshTimer.unref === 'function') refreshTimer.unref();
}

function getCachedStabilityStatus() {
  if (cachedSnapshot) return cachedSnapshot;
  const live = buildStabilityStatus();
  live.snapshotSource = 'live';
  return live;
}

function getCachedStabilityJson() {
  if (cachedJson) return cachedJson;
  return JSON.stringify(getCachedStabilityStatus());
}

function _resetForTests() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  cachedSnapshot = null;
  cachedJson = null;
  cachedAt = null;
}

module.exports = {
  startStabilitySnapshotLoop,
  refreshStabilitySnapshot,
  getCachedStabilityStatus,
  getCachedStabilityJson,
  _resetForTests,
};
