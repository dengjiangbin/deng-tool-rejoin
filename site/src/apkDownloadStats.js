'use strict';
/**
 * Durable APK download counter (file-backed, PM2-safe).
 * GET on /downloads/*.apk increments; HEAD does not.
 */

const fs = require('fs');
const path = require('path');

const STATS_PATH = process.env.APK_DOWNLOAD_STATS_PATH
  || path.join(__dirname, '..', 'data', 'apk_download_stats.json');

function _read() {
  try {
    if (!fs.existsSync(STATS_PATH)) return { versions: {}, latest: null };
    const parsed = JSON.parse(fs.readFileSync(STATS_PATH, 'utf8'));
    return parsed && typeof parsed === 'object' ? parsed : { versions: {}, latest: null };
  } catch (_) {
    return { versions: {}, latest: null };
  }
}

function _write(data) {
  const dir = path.dirname(STATS_PATH);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = `${STATS_PATH}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
  fs.renameSync(tmp, STATS_PATH);
}

function _versionFromFilename(fileName) {
  const m = String(fileName || '').match(/v(\d+\.\d+\.\d+)/i);
  return m ? m[1] : null;
}

/** Record a download for a served APK file. */
function recordDownload(fileName) {
  const base = path.basename(String(fileName || ''));
  if (!base.endsWith('.apk')) return;
  const data = _read();
  const version = _versionFromFilename(base);
  const key = version || base;
  if (!data.versions[key]) {
    data.versions[key] = { file_name: base, version: version || key, downloads: 0 };
  }
  data.versions[key].downloads = Number(data.versions[key].downloads || 0) + 1;
  data.versions[key].file_name = base;
  data.versions[key].updated_at = new Date().toISOString();
  data.latest = {
    file_name: base,
    version: version || key,
    downloads: data.versions[key].downloads,
    updated_at: data.versions[key].updated_at,
  };
  _write(data);
}

/** Public stats for the download page + API. */
function getStats() {
  const data = _read();
  const latest = data.latest || null;
  return {
    ok: true,
    latest: latest ? {
      version: latest.version,
      file_name: latest.file_name,
      downloads: Number(latest.downloads || 0),
    } : null,
  };
}

/** Test seam */
function _reset() {
  try { fs.unlinkSync(STATS_PATH); } catch (_) { /* ok */ }
}

module.exports = { STATS_PATH, recordDownload, getStats, _reset, _versionFromFilename };
