'use strict';
/**
 * Durable download counters per platform (file-backed, PM2-safe).
 * GET on served binaries increments; HEAD does not.
 */

const fs = require('fs');
const path = require('path');

const DEFAULT_PATH = path.join(__dirname, '..', 'data', 'download_stats.json');
const RELEASES_ROOT = path.join(__dirname, '..', '..', 'releases');

const PLATFORM_CONFIG = {
  android: {
    statsPath: process.env.APK_DOWNLOAD_STATS_PATH || process.env.ANDROID_DOWNLOAD_STATS_PATH,
    ext: '.apk',
    legacyPath: path.join(__dirname, '..', 'data', 'apk_download_stats.json'),
  },
  ios: {
    statsPath: process.env.IOS_DOWNLOAD_STATS_PATH,
    ext: '.ipa',
    legacyPath: null,
  },
};

function _statsFile() {
  return process.env.DOWNLOAD_STATS_PATH
    || process.env.APK_DOWNLOAD_STATS_PATH
    || process.env.ANDROID_DOWNLOAD_STATS_PATH
    || process.env.IOS_DOWNLOAD_STATS_PATH
    || DEFAULT_PATH;
}

function _legacyApkPath() {
  return path.join(__dirname, '..', 'data', 'apk_download_stats.json');
}

function _read() {
  const file = _statsFile();
  if (file === DEFAULT_PATH && fs.existsSync(_legacyApkPath()) && !fs.existsSync(DEFAULT_PATH)) {
    return _readFile(_legacyApkPath());
  }
  return _readFile(file);
}

function _readFile(file) {
  try {
    if (!fs.existsSync(file)) return { platforms: {}, android: null, ios: null };
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!parsed || typeof parsed !== 'object') return { platforms: {}, android: null, ios: null };
    // Migrate legacy single-platform APK file shape.
    if (parsed.versions && !parsed.platforms) {
      return {
        platforms: {
          android: {
            versions: parsed.versions,
            latest: parsed.latest || null,
          },
        },
        android: parsed.latest || null,
        ios: null,
      };
    }
    const platforms = parsed.platforms && typeof parsed.platforms === 'object'
      ? parsed.platforms : {};
    return {
      platforms,
      android: platforms.android?.latest || null,
      ios: platforms.ios?.latest || null,
    };
  } catch (_) {
    return { platforms: {}, android: null, ios: null };
  }
}

function _write(platform, data) {
  const file = _statsFile();
  const dir = path.dirname(file);
  fs.mkdirSync(dir, { recursive: true });
  const all = _read();
  const platforms = { ...all.platforms };
  const bucket = platforms[platform] || { versions: {}, latest: null };
  bucket.latest = data.latest;
  bucket.versions = data.versions;
  platforms[platform] = bucket;
  const payload = { platforms, updated_at: new Date().toISOString() };
  const tmp = `${file}.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), 'utf8');
  fs.renameSync(tmp, file);
}

function _versionFromFilename(fileName) {
  const m = String(fileName || '').match(/v(\d+\.\d+\.\d+)/i);
  return m ? m[1] : null;
}

function _publishedLatest(platform) {
  const rel = platform === 'ios'
    ? path.join(RELEASES_ROOT, 'ios', 'latest.json')
    : path.join(RELEASES_ROOT, 'android', 'latest.json');
  try {
    if (!fs.existsSync(rel)) return null;
    const raw = JSON.parse(fs.readFileSync(rel, 'utf8'));
    const fileName = String(raw.file_name || '');
    const version = String(raw.version_name || _versionFromFilename(fileName) || '');
    if (!fileName || !version) return null;
    return { file_name: fileName, version };
  } catch (_) {
    return null;
  }
}

/** Record a download for a platform binary. */
function recordDownload(platform, fileName) {
  const p = platform === 'ios' ? 'ios' : 'android';
  const cfg = PLATFORM_CONFIG[p];
  const base = path.basename(String(fileName || ''));
  if (!base.toLowerCase().endsWith(cfg.ext)) return;

  const all = _read();
  const bucket = all.platforms[p] || { versions: {}, latest: null };
  const versions = { ...(bucket.versions || {}) };
  const version = _versionFromFilename(base);
  const key = version || base;
  if (!versions[key]) {
    versions[key] = { file_name: base, version: version || key, downloads: 0 };
  }
  versions[key].downloads = Number(versions[key].downloads || 0) + 1;
  versions[key].file_name = base;
  versions[key].updated_at = new Date().toISOString();
  const published = _publishedLatest(p);
  const latestKey = published ? published.version : key;
  const latestRow = versions[latestKey] || {
    file_name: published?.file_name || base,
    version: published?.version || version || key,
    downloads: 0,
  };
  const latest = {
    file_name: published?.file_name || latestRow.file_name || base,
    version: published?.version || latestRow.version || latestKey,
    downloads: Number(latestRow.downloads || 0),
    updated_at: latestRow.updated_at || null,
  };
  _write(p, { versions, latest });
}

function _latestFor(platform) {
  const all = _read();
  const bucket = all.platforms[platform];
  const published = _publishedLatest(platform);
  let latest = bucket?.latest || (platform === 'android' ? all.android : all.ios);
  if (published) {
    const row = bucket?.versions?.[published.version];
    latest = {
      file_name: published.file_name,
      version: published.version,
      downloads: Number(row?.downloads || 0),
    };
  }
  if (!latest) return null;
  return {
    version: latest.version,
    file_name: latest.file_name,
    downloads: Number(latest.downloads || 0),
  };
}

/** Stats for one platform. */
function getPlatformStats(platform) {
  const latest = _latestFor(platform === 'ios' ? 'ios' : 'android');
  return { ok: true, platform, latest };
}

/** Combined public stats for download page. */
function getAllStats() {
  return {
    ok: true,
    android: _latestFor('android'),
    ios: _latestFor('ios'),
  };
}

/** Back-compat: APK-only API shape. */
function getApkStats() {
  const latest = _latestFor('android');
  return { ok: true, latest };
}

function recordApkDownload(fileName) {
  recordDownload('android', fileName);
}

function _reset() {
  try { fs.unlinkSync(_statsFile()); } catch (_) { /* ok */ }
}

module.exports = {
  DEFAULT_PATH,
  recordDownload,
  recordApkDownload,
  getPlatformStats,
  getAllStats,
  getApkStats,
  _reset,
  _versionFromFilename,
};
