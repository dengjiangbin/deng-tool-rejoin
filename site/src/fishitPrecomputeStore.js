'use strict';

/**
 * Precomputed per-user tracker snapshot cache (Phase 4).
 *
 * The background worker (deng-tracker-worker) writes one precomputed
 * get-backpack payload per session here; the read API (deng-tracker-read,
 * port 8793) serves it directly with no recompute and no image resolution.
 *
 * Storage: node:sqlite DatabaseSync with WAL (same engine the site already
 * uses for fishit_global.db). The latest snapshot is UPSERTed per session_key
 * so the cache never grows unbounded on the "latest" table; a bounded history
 * table keeps the last N rows per session within a TTL for debugging only.
 *
 * Hard rules honored here:
 *   - Latest snapshot overwrites per session_key (UPSERT) — no unlimited growth.
 *   - History is bounded by both last-N-per-session and a TTL.
 *   - No image binaries are stored in SQLite (only owned local URLs inside JSON).
 */

const path = require('path');
const fs = require('fs');
const { parseTimestampMs } = require('./trackerAccountPresence');

let DatabaseSync = null;
try {
  ({ DatabaseSync } = require('node:sqlite'));
} catch (err) {
  // node:sqlite is available on Node >= 22.5 (this host runs v24). If it is ever
  // missing we surface a clear error on first use rather than at require time.
  DatabaseSync = null;
}

const DEFAULT_DB_PATH = path.join(__dirname, '..', 'data', 'fishit_precompute.db');

function dbPath() {
  return process.env.FISHIT_PRECOMPUTE_DB_PATH || DEFAULT_DB_PATH;
}

// History bounds (Phase 4: "max 10–20 snapshots per user OR 24–48h TTL").
const HISTORY_MAX_PER_SESSION = parseInt(process.env.FISHIT_PRECOMPUTE_HISTORY_MAX || '15', 10);
const HISTORY_TTL_MS = parseInt(process.env.FISHIT_PRECOMPUTE_HISTORY_TTL_MS || String(36 * 60 * 60 * 1000), 10);

let _db = null;
let _stmts = null;

function openDb() {
  if (_db) return _db;
  if (!DatabaseSync) {
    throw new Error('node:sqlite DatabaseSync is unavailable; cannot open precompute store');
  }
  const target = dbPath();
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const db = new DatabaseSync(target);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA synchronous = NORMAL;');
  db.exec('PRAGMA busy_timeout = 5000;');
  migrate(db);
  _db = db;
  _stmts = prepareStatements(db);
  return _db;
}

function migrate(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS tracker_latest_snapshots (
      session_key TEXT PRIMARY KEY,
      username TEXT,
      user_id TEXT,
      latest_precomputed_json TEXT,
      precomputed_hash TEXT,
      raw_hash TEXT,
      ruby_gemstone_count INTEGER DEFAULT 0,
      fish_type_count INTEGER DEFAULT 0,
      build_ms INTEGER DEFAULT 0,
      last_upload_at TEXT,
      last_inventory_at TEXT,
      last_precomputed_at TEXT,
      updated_at TEXT
    );
  `);
  db.exec(`
    CREATE TABLE IF NOT EXISTS tracker_snapshot_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_key TEXT NOT NULL,
      precomputed_hash TEXT,
      ruby_gemstone_count INTEGER DEFAULT 0,
      created_at TEXT,
      created_ms INTEGER
    );
  `);
  // Presence decoupling (2026-06-18): a tiny per-account presence record that the
  // worker refreshes on EVERY real heartbeat — even when the inventory body is
  // byte-stable (churn-skip). Without this, a heartbeat-only upload never bumps
  // precomputed_hash, so the read lane never refreshes presence and an actively
  // uploading account shows stale "offline" with a frozen age. presence_json
  // carries only the small presence/age fields; last_precomputed_at is bumped so
  // the read lane's existing change probe picks it up WITHOUT pulling the blob.
  const cols = db.prepare('PRAGMA table_info(tracker_latest_snapshots)').all().map((c) => c.name);
  if (!cols.includes('presence_json')) {
    try {
      db.exec('ALTER TABLE tracker_latest_snapshots ADD COLUMN presence_json TEXT;');
    } catch (err) {
      // Another process (worker vs read) may have added it first — that's fine.
      if (!/duplicate column/i.test(err && err.message ? err.message : '')) throw err;
    }
  }
  db.exec('CREATE INDEX IF NOT EXISTS idx_latest_username ON tracker_latest_snapshots(username);');
  db.exec('CREATE INDEX IF NOT EXISTS idx_latest_user_id ON tracker_latest_snapshots(user_id);');
  db.exec('CREATE INDEX IF NOT EXISTS idx_latest_precomputed_at ON tracker_latest_snapshots(last_precomputed_at);');
  db.exec('CREATE INDEX IF NOT EXISTS idx_history_session ON tracker_snapshot_history(session_key, created_ms);');
  db.exec('CREATE INDEX IF NOT EXISTS idx_history_created ON tracker_snapshot_history(created_ms);');
}

function prepareStatements(db) {
  return {
    upsertLatest: db.prepare(`
      INSERT INTO tracker_latest_snapshots
        (session_key, username, user_id, latest_precomputed_json, precomputed_hash, raw_hash,
         ruby_gemstone_count, fish_type_count, build_ms, last_upload_at, last_inventory_at,
         last_precomputed_at, updated_at, presence_json)
      VALUES
        (@session_key, @username, @user_id, @latest_precomputed_json, @precomputed_hash, @raw_hash,
         @ruby_gemstone_count, @fish_type_count, @build_ms, @last_upload_at, @last_inventory_at,
         @last_precomputed_at, @updated_at, @presence_json)
      ON CONFLICT(session_key) DO UPDATE SET
        username = excluded.username,
        user_id = excluded.user_id,
        latest_precomputed_json = excluded.latest_precomputed_json,
        precomputed_hash = excluded.precomputed_hash,
        raw_hash = excluded.raw_hash,
        ruby_gemstone_count = excluded.ruby_gemstone_count,
        fish_type_count = excluded.fish_type_count,
        build_ms = excluded.build_ms,
        last_upload_at = excluded.last_upload_at,
        last_inventory_at = excluded.last_inventory_at,
        last_precomputed_at = excluded.last_precomputed_at,
        updated_at = excluded.updated_at,
        presence_json = excluded.presence_json
    `),
    // Cheap presence-only refresh: updates the tiny presence record and bumps
    // last_precomputed_at (the read lane's change-probe watermark) WITHOUT
    // rewriting the multi-hundred-KB inventory JSON or changing precomputed_hash.
    // Used on every real heartbeat so an online account never reads stale.
    updatePresence: db.prepare(`
      UPDATE tracker_latest_snapshots
      SET presence_json = @presence_json,
          last_precomputed_at = @last_precomputed_at,
          updated_at = @updated_at
      WHERE session_key = @session_key
    `),
    getLatestJson: db.prepare(
      'SELECT latest_precomputed_json, precomputed_hash, raw_hash, ruby_gemstone_count, last_precomputed_at, last_upload_at, last_inventory_at, username, user_id FROM tracker_latest_snapshots WHERE session_key = ?',
    ),
    getMeta: db.prepare(
      'SELECT session_key, raw_hash, precomputed_hash, last_upload_at, last_precomputed_at, ruby_gemstone_count FROM tracker_latest_snapshots WHERE session_key = ?',
    ),
    getUserIdAlias: db.prepare(
      'SELECT session_key FROM tracker_latest_snapshots WHERE user_id = ? LIMIT 1',
    ),
    allMeta: db.prepare(
      'SELECT session_key, raw_hash, last_upload_at, last_precomputed_at FROM tracker_latest_snapshots',
    ),
    allRowsForCache: db.prepare(
      'SELECT session_key, user_id, latest_precomputed_json, precomputed_hash, last_precomputed_at, presence_json FROM tracker_latest_snapshots',
    ),
    warmBatchRows: db.prepare(
      'SELECT session_key, user_id, latest_precomputed_json, precomputed_hash, last_precomputed_at, presence_json FROM tracker_latest_snapshots ORDER BY session_key LIMIT ? OFFSET ?',
    ),
    changedSince: db.prepare(
      'SELECT session_key, user_id, latest_precomputed_json, last_precomputed_at FROM tracker_latest_snapshots WHERE last_precomputed_at >= ?',
    ),
    // Lightweight change probe: NO json blob. The read lane uses this every
    // refresh tick to detect which sessions actually changed (by hash) before
    // pulling any multi-hundred-KB JSON. This keeps the synchronous node:sqlite
    // read off the hot serving event loop in steady state.
    changedMetaSince: db.prepare(
      'SELECT session_key, user_id, precomputed_hash, last_precomputed_at, presence_json FROM tracker_latest_snapshots WHERE last_precomputed_at >= ?',
    ),
    getJsonByKey: db.prepare(
      'SELECT latest_precomputed_json, precomputed_hash, last_precomputed_at, user_id FROM tracker_latest_snapshots WHERE session_key = ?',
    ),
    getCacheRowByKey: db.prepare(
      'SELECT session_key, user_id, latest_precomputed_json, precomputed_hash, last_precomputed_at, presence_json FROM tracker_latest_snapshots WHERE session_key = ?',
    ),
    count: db.prepare('SELECT COUNT(*) AS n FROM tracker_latest_snapshots'),
    insertHistory: db.prepare(
      'INSERT INTO tracker_snapshot_history (session_key, precomputed_hash, ruby_gemstone_count, created_at, created_ms) VALUES (?, ?, ?, ?, ?)',
    ),
    trimHistoryPerSession: db.prepare(`
      DELETE FROM tracker_snapshot_history
      WHERE session_key = ?
        AND id NOT IN (
          SELECT id FROM tracker_snapshot_history
          WHERE session_key = ?
          ORDER BY created_ms DESC
          LIMIT ?
        )
    `),
    deleteHistoryOlderThan: db.prepare('DELETE FROM tracker_snapshot_history WHERE created_ms < ?'),
    historyCount: db.prepare('SELECT COUNT(*) AS n FROM tracker_snapshot_history'),
  };
}

function inventoryIdentityFromPresenceJson(presenceJson) {
  if (!presenceJson || typeof presenceJson !== 'string') {
    return { invMs: null, revision: null };
  }
  try {
    const p = JSON.parse(presenceJson);
    const invMs = parseTimestampMs(p.lastRealInventoryAt);
    const revision = p.inventoryRevision != null ? Number(p.inventoryRevision) : null;
    return {
      invMs: invMs != null ? invMs : null,
      revision: Number.isFinite(revision) ? revision : null,
    };
  } catch (_) {
    return { invMs: null, revision: null };
  }
}

function incomingInventoryIsStale(existingRow, incomingPresenceJson) {
  if (!existingRow) return false;
  const existing = inventoryIdentityFromPresenceJson(existingRow.presence_json);
  const incoming = inventoryIdentityFromPresenceJson(incomingPresenceJson);
  if (existing.revision != null && incoming.revision != null && incoming.revision < existing.revision) {
    return true;
  }
  if (existing.invMs != null && incoming.invMs != null && incoming.invMs < existing.invMs) {
    return true;
  }
  return false;
}

/**
 * UPSERT the latest precomputed snapshot for one session. `precomputedBody`
 * is the full get-backpack object; it is JSON-stringified for storage.
 */
function upsertLatest(entry) {
  openDb();
  const nowIso = new Date().toISOString();
  const sessionKey = String(entry.sessionKey);
  let existingRow = null;
  try {
    existingRow = _stmts.getCacheRowByKey.get(sessionKey);
  } catch (_) { /* proceed */ }
  if (incomingInventoryIsStale(existingRow, entry.presenceJson || null)) {
    // Never clobber a newer inventory snapshot with stale worker output.
    if (entry.presenceJson) {
      try { updatePresence(sessionKey, entry.presenceJson); } catch (_) { /* non-fatal */ }
    }
    return existingRow && existingRow.last_precomputed_at ? existingRow.last_precomputed_at : nowIso;
  }
  const json = typeof entry.precomputedJson === 'string'
    ? entry.precomputedJson
    : JSON.stringify(entry.precomputedBody || {});
  _stmts.upsertLatest.run({
    session_key: sessionKey,
    username: entry.username || null,
    user_id: entry.userId != null ? String(entry.userId) : null,
    latest_precomputed_json: json,
    precomputed_hash: entry.precomputedHash || null,
    raw_hash: entry.rawHash || null,
    ruby_gemstone_count: Number.isFinite(entry.rubyGemstoneCount) ? entry.rubyGemstoneCount : 0,
    fish_type_count: Number.isFinite(entry.fishTypeCount) ? entry.fishTypeCount : 0,
    build_ms: Number.isFinite(entry.buildMs) ? Math.round(entry.buildMs) : 0,
    last_upload_at: entry.lastUploadAt || null,
    last_inventory_at: entry.lastInventoryAt || null,
    last_precomputed_at: nowIso,
    updated_at: nowIso,
    presence_json: entry.presenceJson || null,
  });
  return nowIso;
}

/**
 * Cheap presence-only refresh. Writes the small presence record and advances
 * the read lane's change-probe watermark (last_precomputed_at) WITHOUT touching
 * the heavy inventory JSON or precomputed_hash. Returns the ISO timestamp used.
 */
function updatePresence(sessionKey, presenceJson) {
  openDb();
  const nowIso = new Date().toISOString();
  _stmts.updatePresence.run({
    session_key: String(sessionKey),
    presence_json: presenceJson || null,
    last_precomputed_at: nowIso,
    updated_at: nowIso,
  });
  return nowIso;
}

/** Append a bounded history row and enforce last-N + TTL. */
function recordHistory(sessionKey, precomputedHash, rubyCount) {
  openDb();
  const nowMs = Date.now();
  _stmts.insertHistory.run(String(sessionKey), precomputedHash || null, Number.isFinite(rubyCount) ? rubyCount : 0, new Date(nowMs).toISOString(), nowMs);
  _stmts.trimHistoryPerSession.run(String(sessionKey), String(sessionKey), HISTORY_MAX_PER_SESSION);
}

/** Cleanup job (Phase 4): delete history older than TTL. Returns rows removed. */
function cleanupHistory() {
  openDb();
  const cutoff = Date.now() - HISTORY_TTL_MS;
  const res = _stmts.deleteHistoryOlderThan.run(cutoff);
  return res && typeof res.changes === 'number' ? res.changes : 0;
}

/** Fast read path: returns the parsed precomputed body (or null). */
function getLatest(sessionKey) {
  openDb();
  const row = _stmts.getLatestJson.get(String(sessionKey));
  if (!row || !row.latest_precomputed_json) return null;
  let body;
  try {
    body = JSON.parse(row.latest_precomputed_json);
  } catch (_) {
    return null;
  }
  return {
    body,
    precomputedHash: row.precomputed_hash,
    rawHash: row.raw_hash,
    rubyGemstoneCount: row.ruby_gemstone_count,
    lastPrecomputedAt: row.last_precomputed_at,
    lastUploadAt: row.last_upload_at,
    lastInventoryAt: row.last_inventory_at,
    username: row.username,
    userId: row.user_id,
  };
}

/**
 * Hot read path: return the stored JSON string WITHOUT parsing it, plus the
 * small metadata columns. Avoids a multi-hundred-KB JSON.parse per request.
 */
function getLatestRaw(sessionKey) {
  openDb();
  const row = _stmts.getLatestJson.get(String(sessionKey));
  if (!row || !row.latest_precomputed_json) return null;
  return {
    json: row.latest_precomputed_json,
    precomputedHash: row.precomputed_hash,
    rubyGemstoneCount: row.ruby_gemstone_count,
    lastPrecomputedAt: row.last_precomputed_at,
    lastUploadAt: row.last_upload_at,
    lastInventoryAt: row.last_inventory_at,
    username: row.username,
    userId: row.user_id,
  };
}

/** Resolve a numeric userId to its session_key, if stored. */
function resolveUserIdAlias(userId) {
  openDb();
  const row = _stmts.getUserIdAlias.get(String(userId));
  return row ? row.session_key : null;
}

function getMeta(sessionKey) {
  openDb();
  return _stmts.getMeta.get(String(sessionKey)) || null;
}

/**
 * Return every precomputed snapshot row for warm-loading an in-memory read cache.
 * Used once at read-process startup; keep the projection tiny to bound memory.
 */
function getAllRowsForCache() {
  openDb();
  return _stmts.allRowsForCache.all();
}

/** Paginated warm-load rows — one SQLite round-trip per batch instead of N getJsonByKey calls. */
function getWarmBatchRows(limit, offset) {
  openDb();
  const lim = Math.max(1, Math.min(Number(limit) || 200, 500));
  const off = Math.max(0, Number(offset) || 0);
  return _stmts.warmBatchRows.all(lim, off);
}

function countLatestSnapshots() {
  openDb();
  const row = _stmts.count.get();
  return row && row.n != null ? Number(row.n) : 0;
}

/**
 * Return rows whose last_precomputed_at is >= the given ISO timestamp.
 * last_precomputed_at is an ISO-8601 string, so lexicographic >= equals
 * chronological >=. Boundary rows are re-fetched (>=) so no change is missed
 * across same-millisecond writes; callers dedupe by session_key.
 */
function getChangedSince(iso) {
  openDb();
  return _stmts.changedSince.all(String(iso || ''));
}

/**
 * Lightweight metadata-only change probe (no JSON blob). Returns
 * { session_key, user_id, precomputed_hash, last_precomputed_at } for rows at
 * or after the watermark. The read lane compares precomputed_hash against its
 * cached value and only pulls the full JSON (getJsonByKey) for rows that truly
 * changed — so an idle/unchanged fleet costs a few tiny rows per refresh
 * instead of moving every snapshot's multi-hundred-KB JSON synchronously.
 */
function getChangedMetaSince(iso) {
  openDb();
  return _stmts.changedMetaSince.all(String(iso || ''));
}

/** Single-row JSON fetch for a session that the read lane detected as changed. */
function getJsonByKey(sessionKey) {
  openDb();
  const row = _stmts.getJsonByKey.get(String(sessionKey));
  if (!row || !row.latest_precomputed_json) return null;
  return {
    json: row.latest_precomputed_json,
    precomputedHash: row.precomputed_hash || '',
    lastPrecomputedAt: row.last_precomputed_at || '',
    userId: row.user_id,
  };
}

function allMeta() {
  openDb();
  return _stmts.allMeta.all();
}

function getCacheRowByKey(sessionKey) {
  openDb();
  return _stmts.getCacheRowByKey.get(String(sessionKey)) || null;
}

function getStoreStats() {
  openDb();
  const latest = _stmts.count.get();
  const history = _stmts.historyCount.get();
  let fileBytes = 0;
  try { fileBytes = fs.statSync(dbPath()).size; } catch (_) { fileBytes = 0; }
  return {
    dbPath: dbPath(),
    latestRows: latest ? latest.n : 0,
    historyRows: history ? history.n : 0,
    fileBytes,
    historyMaxPerSession: HISTORY_MAX_PER_SESSION,
    historyTtlMs: HISTORY_TTL_MS,
  };
}

function close() {
  if (_db) {
    try { _db.close(); } catch (_) { /* ignore */ }
  }
  _db = null;
  _stmts = null;
}

module.exports = {
  dbPath,
  openDb,
  upsertLatest,
  updatePresence,
  recordHistory,
  cleanupHistory,
  getLatest,
  getLatestRaw,
  getMeta,
  allMeta,
  getAllRowsForCache,
  getWarmBatchRows,
  countLatestSnapshots,
  getChangedSince,
  getChangedMetaSince,
  getJsonByKey,
  getCacheRowByKey,
  resolveUserIdAlias,
  getStoreStats,
  close,
  HISTORY_MAX_PER_SESSION,
  HISTORY_TTL_MS,
};
