'use strict';

const fs = require('fs');

const WARN_BYTES = Number(process.env.DISK_WARN_FREE_BYTES || 5 * 1024 ** 3);
const CRIT_BYTES = Number(process.env.DISK_CRIT_FREE_BYTES || 1 * 1024 ** 3);
const CACHE_TTL_MS = Number(process.env.DISK_MONITOR_CACHE_MS || 60_000);

let cachedStatus = null;
let cachedAt = 0;

function levelForFreeBytes(free) {
  if (free <= CRIT_BYTES) return 'critical';
  if (free <= WARN_BYTES) return 'warning';
  return 'ok';
}

function driveEntry(drive, free, size) {
  return {
    drive,
    freeBytes: free,
    sizeBytes: size,
    freeGb: Math.round((free / 1024 ** 3) * 100) / 100,
    usedGb: Math.round(((size - free) / 1024 ** 3) * 100) / 100,
    level: levelForFreeBytes(free),
  };
}

function getWindowsDriveStatuses() {
  const drives = [];
  for (let i = 0; i < 26; i += 1) {
    const letter = String.fromCharCode(65 + i);
    const root = `${letter}:\\`;
    try {
      fs.accessSync(root, fs.constants.F_OK);
      const st = fs.statfsSync(root);
      const free = Number(st.bsize) * Number(st.bavail);
      const size = Number(st.bsize) * Number(st.blocks);
      if (!Number.isFinite(free) || !Number.isFinite(size) || size <= 0) continue;
      drives.push(driveEntry(`${letter}:`, free, size));
    } catch {
      // drive not mounted or inaccessible
    }
  }
  return drives;
}

function getUnixMountStatuses() {
  const drives = [];
  for (const mount of ['/', '/tmp']) {
    try {
      const st = fs.statfsSync(mount);
      const free = Number(st.bsize) * Number(st.bavail);
      const size = Number(st.bsize) * Number(st.blocks);
      if (!Number.isFinite(free) || !Number.isFinite(size) || size <= 0) continue;
      drives.push(driveEntry(mount, free, size));
    } catch {
      // ignore missing mount
    }
  }
  return drives;
}

function getDiskFreeStatusFresh() {
  try {
    if (process.platform === 'win32') {
      const drives = getWindowsDriveStatuses();
      return { drives, source: 'statfs' };
    }
    return { drives: getUnixMountStatuses(), source: 'statfs' };
  } catch (err) {
    return { drives: [], error: err.message, source: 'error' };
  }
}

function getDiskFreeStatus() {
  const now = Date.now();
  if (cachedStatus && (now - cachedAt) < CACHE_TTL_MS) {
    return cachedStatus;
  }
  cachedStatus = getDiskFreeStatusFresh();
  cachedAt = now;
  return cachedStatus;
}

function isDriveUsedForWrites(driveLetter) {
  const letter = String(driveLetter || '').toUpperCase().replace(':', '');
  const checks = [
    process.env.TOOL_SITE_SESSION_DIR,
    process.env.FISHIT_LIVE_SESSIONS_PATH,
    process.env.FISHIT_DB_PATH,
    process.env.PM2_HOME,
  ].filter(Boolean);
  return checks.some((p) => String(p).toUpperCase().startsWith(`${letter}:`));
}

function _resetCacheForTests() {
  cachedStatus = null;
  cachedAt = 0;
}

module.exports = {
  getDiskFreeStatus,
  getDiskFreeStatusFresh,
  isDriveUsedForWrites,
  WARN_BYTES,
  CRIT_BYTES,
  _resetCacheForTests,
};
