'use strict';

const fs = require('fs');
const { execFileSync } = require('child_process');

const WARN_BYTES = Number(process.env.DISK_WARN_FREE_BYTES || 5 * 1024 ** 3);
const CRIT_BYTES = Number(process.env.DISK_CRIT_FREE_BYTES || 1 * 1024 ** 3);

function parseWmicOutput(text) {
  const lines = String(text || '').trim().split(/\r?\n/).slice(1);
  const drives = [];
  for (const line of lines) {
    const parts = line.trim().split(/\s+/);
    if (parts.length < 3) continue;
    const caption = parts[0];
    const free = Number(parts[1]);
    const size = Number(parts[2]);
    if (!caption || !Number.isFinite(free)) continue;
    drives.push({
      drive: caption,
      freeBytes: free,
      sizeBytes: size,
      freeGb: Math.round((free / 1024 ** 3) * 100) / 100,
      usedGb: Math.round(((size - free) / 1024 ** 3) * 100) / 100,
      level: free <= CRIT_BYTES ? 'critical' : (free <= WARN_BYTES ? 'warning' : 'ok'),
    });
  }
  return drives;
}

function getDiskFreeStatus() {
  try {
    if (process.platform === 'win32') {
      const out = execFileSync('wmic', ['logicaldisk', 'get', 'caption,freespace,size'], {
        encoding: 'utf8',
        timeout: 10_000,
      });
      return { drives: parseWmicOutput(out), source: 'wmic' };
    }
    const drives = [];
    for (const mount of ['/', '/tmp']) {
      try {
        const st = fs.statfsSync(mount);
        const free = st.bsize * st.bavail;
        const size = st.bsize * st.blocks;
        drives.push({
          drive: mount,
          freeBytes: free,
          sizeBytes: size,
          freeGb: Math.round((free / 1024 ** 3) * 100) / 100,
          usedGb: Math.round(((size - free) / 1024 ** 3) * 100) / 100,
          level: free <= CRIT_BYTES ? 'critical' : (free <= WARN_BYTES ? 'warning' : 'ok'),
        });
      } catch {
        // ignore missing mount
      }
    }
    return { drives, source: 'statfs' };
  } catch (err) {
    return { drives: [], error: err.message, source: 'error' };
  }
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

module.exports = {
  getDiskFreeStatus,
  isDriveUsedForWrites,
  WARN_BYTES,
  CRIT_BYTES,
};
