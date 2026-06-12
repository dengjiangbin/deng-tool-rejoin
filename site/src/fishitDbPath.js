'use strict';

const fs = require('fs');
const path = require('path');

const SITE_ROOT = path.join(__dirname, '..');
const REPO_ROOT = path.join(__dirname, '..', '..');

function envPath() {
  const raw = process.env.FISHIT_DB_PATH;
  return raw && String(raw).trim() ? path.resolve(String(raw).trim()) : null;
}

/** Candidate paths in priority order (DENG Fish It bot catch-record SQLite). */
function candidateDbPaths() {
  const fromEnv = envPath();
  const list = [
    fromEnv,
    path.join(SITE_ROOT, 'data', 'deng-fish-it.sqlite'),
    path.join(REPO_ROOT, 'DENG Fish It', 'data', 'deng-fish-it.sqlite'),
    path.join(process.cwd(), 'data', 'deng-fish-it.sqlite'),
    path.join(process.cwd(), '..', 'DENG Fish It', 'data', 'deng-fish-it.sqlite'),
    path.join(process.cwd(), 'DENG Fish It', 'data', 'deng-fish-it.sqlite'),
    '/var/lib/deng-fish-it/deng-fish-it.sqlite',
    '/opt/deng-fish-it/data/deng-fish-it.sqlite',
  ].filter(Boolean);
  const seen = new Set();
  return list.filter((p) => {
    const key = path.resolve(p);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function fileExists(p) {
  try {
    return fs.existsSync(p) && fs.statSync(p).isFile();
  } catch (_) {
    return false;
  }
}

let _resolvedPath = null;
let _resolvedAt = 0;
let _lastProbe = null;

function resolveFishitDbPath(force) {
  const fromEnv = envPath();
  if (fromEnv) {
    const resolved = path.resolve(fromEnv);
    if (!force && _resolvedPath === resolved) return _resolvedPath;
    _resolvedPath = resolved;
    _resolvedAt = Date.now();
    return _resolvedPath;
  }
  if (!force && _resolvedPath && fileExists(_resolvedPath)) return _resolvedPath;
  const candidates = candidateDbPaths();
  for (const candidate of candidates) {
    if (fileExists(candidate)) {
      _resolvedPath = path.resolve(candidate);
      _resolvedAt = Date.now();
      return _resolvedPath;
    }
  }
  _resolvedPath = fromEnvOrDefault();
  _resolvedAt = Date.now();
  return _resolvedPath;
}

function fromEnvOrDefault() {
  return envPath() || path.join(REPO_ROOT, 'DENG Fish It', 'data', 'deng-fish-it.sqlite');
}

function probeFishitDb(dbPath) {
  const out = {
    dbPath: dbPath || fromEnvOrDefault(),
    exists: false,
    readable: false,
    hasAppKv: false,
    hasFishCache: false,
    fishCacheBytes: 0,
    error: null,
  };
  if (!fileExists(out.dbPath)) {
    out.error = 'file_not_found';
    return out;
  }
  out.exists = true;
  try {
    const { DatabaseSync } = require('node:sqlite');
    const db = new DatabaseSync(out.dbPath, { readOnly: true });
    out.readable = true;
    const tables = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='app_kv'",
    ).get();
    out.hasAppKv = !!tables;
    if (out.hasAppKv) {
      const row = db.prepare('SELECT length(value) AS len FROM app_kv WHERE key = ?').get('alltime_fish_cache');
      if (row && row.len > 0) {
        out.hasFishCache = true;
        out.fishCacheBytes = Number(row.len) || 0;
      }
    }
  } catch (err) {
    out.error = err && err.message ? err.message : String(err);
  }
  _lastProbe = { ...out, probedAt: new Date().toISOString() };
  return out;
}

function getDbStatus(forceProbe) {
  const dbPath = resolveFishitDbPath(forceProbe);
  if (forceProbe || !_lastProbe || _lastProbe.dbPath !== dbPath) {
    return probeFishitDb(dbPath);
  }
  return { ..._lastProbe, dbPath };
}

function invalidateResolvedPath() {
  _resolvedPath = null;
  _resolvedAt = 0;
  _lastProbe = null;
}

module.exports = {
  SITE_ROOT,
  REPO_ROOT,
  candidateDbPaths,
  resolveFishitDbPath,
  getDbStatus,
  probeFishitDb,
  invalidateResolvedPath,
};
