'use strict';
/**
 * BLOCKER10V — SQLite global collective Fish It catalog (source of truth).
 */

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

const DEFAULT_DB_PATH = path.join(__dirname, '..', 'data', 'fishit_global.db');

function dbPath() {
  return process.env.FISHIT_GLOBAL_DB_PATH || DEFAULT_DB_PATH;
}

const VERIFICATION = {
  SEED_IMPORTED: 'seed_imported',
  LIVE_OBSERVED: 'live_observed',
  MULTI_USER_CONFIRMED: 'multi_user_confirmed',
  MANUAL_VERIFIED: 'manual_verified',
  QUARANTINED_CONFLICT: 'quarantined_conflict',
};

let _db = null;
let _dbPath = null;

function _now() {
  return new Date().toISOString();
}

function closeDb() {
  if (_db) {
    try { _db.close(); } catch (_) { /* ignore */ }
    _db = null;
    _dbPath = null;
  }
}

function openDb() {
  const targetPath = dbPath();
  if (_db && _dbPath !== targetPath) {
    try { _db.close(); } catch (_) { /* ignore */ }
    _db = null;
    _dbPath = null;
  }
  if (_db) return _db;
  const dir = path.dirname(targetPath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  const { DatabaseSync } = require('node:sqlite');
  _db = new DatabaseSync(targetPath);
  _dbPath = targetPath;
  _db.exec('PRAGMA journal_mode = WAL;');
  _db.exec('PRAGMA foreign_keys = ON;');
  _migrate(_db);
  return _db;
}

function _migrate(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS fishit_global_species (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      normalized_name TEXT NOT NULL UNIQUE,
      canonical_name TEXT NOT NULL,
      display_name TEXT,
      rarity TEXT,
      rarity_source TEXT,
      rarity_confidence TEXT,
      image_url TEXT,
      cached_image_url TEXT,
      image_source TEXT,
      quiz_bot_bank_id TEXT,
      quiz_bot_asset_id TEXT,
      source TEXT NOT NULL DEFAULT 'unknown',
      verification_status TEXT NOT NULL DEFAULT 'seed_imported',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS fishit_global_item_mappings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      game_id TEXT,
      place_id TEXT,
      item_id TEXT NOT NULL,
      species_id INTEGER,
      canonical_name TEXT,
      confidence TEXT NOT NULL DEFAULT 'live_observed',
      source TEXT NOT NULL DEFAULT 'live_observed',
      evidence_count INTEGER NOT NULL DEFAULT 0,
      unique_user_count INTEGER NOT NULL DEFAULT 0,
      conflict_status TEXT,
      first_seen_at TEXT,
      last_seen_at TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(item_id)
    );

    CREATE TABLE IF NOT EXISTS fishit_global_observations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      anonymized_user_hash TEXT,
      session_key_hash TEXT,
      game_id TEXT,
      place_id TEXT,
      item_id TEXT,
      raw_name TEXT,
      parsed_base_name TEXT,
      mutation TEXT,
      weight_kg REAL,
      rarity TEXT,
      source_payload_type TEXT,
      observed_at TEXT NOT NULL,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS fishit_global_conflicts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      conflict_type TEXT NOT NULL,
      game_id TEXT,
      place_id TEXT,
      item_id TEXT,
      candidate_names TEXT,
      candidate_species_ids TEXT,
      evidence_summary TEXT,
      status TEXT NOT NULL DEFAULT 'open',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS fishit_global_image_assets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      species_id INTEGER NOT NULL,
      canonical_name TEXT NOT NULL,
      original_source TEXT NOT NULL,
      original_url_or_path TEXT,
      local_cached_url TEXT,
      content_hash TEXT,
      mime_type TEXT,
      width INTEGER,
      height INTEGER,
      status TEXT NOT NULL DEFAULT 'active',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY (species_id) REFERENCES fishit_global_species(id)
    );

    CREATE INDEX IF NOT EXISTS idx_species_normalized ON fishit_global_species(normalized_name);
    CREATE INDEX IF NOT EXISTS idx_species_canonical ON fishit_global_species(canonical_name);
    CREATE INDEX IF NOT EXISTS idx_mapping_item ON fishit_global_item_mappings(item_id);
    CREATE INDEX IF NOT EXISTS idx_mapping_species ON fishit_global_item_mappings(species_id);
    CREATE INDEX IF NOT EXISTS idx_obs_item ON fishit_global_observations(item_id);
    CREATE INDEX IF NOT EXISTS idx_image_species ON fishit_global_image_assets(species_id);
    CREATE INDEX IF NOT EXISTS idx_conflict_item ON fishit_global_conflicts(item_id);
  `);
}

function normalizeName(raw) {
  return String(raw || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function normalizeNamePunct(raw) {
  return normalizeName(raw)
    .replace(/[''`]/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function hashContributor(value, salt = 'fishit_global_v1') {
  if (value == null || value === '') return null;
  return crypto.createHash('sha256').update(`${salt}:${value}`).digest('hex').slice(0, 16);
}

function upsertSpecies(row) {
  const db = openDb();
  const now = _now();
  const normalized = row.normalized_name || normalizeNamePunct(row.canonical_name);
  const existing = db.prepare('SELECT id FROM fishit_global_species WHERE normalized_name = ?').get(normalized);
  if (existing) {
    db.prepare(`
      UPDATE fishit_global_species SET
        canonical_name = COALESCE(?, canonical_name),
        display_name = COALESCE(?, display_name),
        rarity = COALESCE(?, rarity),
        rarity_source = COALESCE(?, rarity_source),
        rarity_confidence = COALESCE(?, rarity_confidence),
        image_url = COALESCE(?, image_url),
        cached_image_url = COALESCE(?, cached_image_url),
        image_source = COALESCE(?, image_source),
        quiz_bot_bank_id = COALESCE(?, quiz_bot_bank_id),
        quiz_bot_asset_id = COALESCE(?, quiz_bot_asset_id),
        source = COALESCE(?, source),
        verification_status = COALESCE(?, verification_status),
        updated_at = ?
      WHERE id = ?
    `).run(
      row.canonical_name || null,
      row.display_name || null,
      row.rarity || null,
      row.rarity_source || null,
      row.rarity_confidence || null,
      row.image_url || null,
      row.cached_image_url || null,
      row.image_source || null,
      row.quiz_bot_bank_id || null,
      row.quiz_bot_asset_id || null,
      row.source || null,
      row.verification_status || null,
      now,
      existing.id,
    );
    return existing.id;
  }
  const result = db.prepare(`
    INSERT INTO fishit_global_species (
      normalized_name, canonical_name, display_name, rarity, rarity_source, rarity_confidence,
      image_url, cached_image_url, image_source, quiz_bot_bank_id, quiz_bot_asset_id,
      source, verification_status, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    normalized,
    row.canonical_name,
    row.display_name || row.canonical_name,
    row.rarity || null,
    row.rarity_source || null,
    row.rarity_confidence || null,
    row.image_url || null,
    row.cached_image_url || null,
    row.image_source || null,
    row.quiz_bot_bank_id || null,
    row.quiz_bot_asset_id || null,
    row.source || 'unknown',
    row.verification_status || VERIFICATION.SEED_IMPORTED,
    now,
    now,
  );
  return Number(result.lastInsertRowid);
}

function getSpeciesById(id) {
  return openDb().prepare('SELECT * FROM fishit_global_species WHERE id = ?').get(id) || null;
}

function getSpeciesByNormalizedName(name) {
  const n = normalizeNamePunct(name) || normalizeName(name);
  if (!n) return null;
  const db = openDb();
  return db.prepare('SELECT * FROM fishit_global_species WHERE normalized_name = ?').get(n)
    || db.prepare('SELECT * FROM fishit_global_species WHERE LOWER(canonical_name) = ?').get(n)
    || null;
}

function findSpeciesByAliases(names) {
  for (const raw of names || []) {
    const hit = getSpeciesByNormalizedName(raw);
    if (hit) return { species: hit, matchedAlias: raw };
  }
  return null;
}

function upsertImageAsset(row) {
  const db = openDb();
  const now = _now();
  const existing = db.prepare(
    'SELECT id FROM fishit_global_image_assets WHERE species_id = ? AND content_hash = ?',
  ).get(row.species_id, row.content_hash || '');
  if (existing) {
    db.prepare(`
      UPDATE fishit_global_image_assets SET
        local_cached_url = COALESCE(?, local_cached_url),
        status = COALESCE(?, status),
        updated_at = ?
      WHERE id = ?
    `).run(row.local_cached_url || null, row.status || 'active', now, existing.id);
    return existing.id;
  }
  const result = db.prepare(`
    INSERT INTO fishit_global_image_assets (
      species_id, canonical_name, original_source, original_url_or_path,
      local_cached_url, content_hash, mime_type, width, height, status, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    row.species_id,
    row.canonical_name,
    row.original_source,
    row.original_url_or_path || null,
    row.local_cached_url || null,
    row.content_hash || null,
    row.mime_type || null,
    row.width || null,
    row.height || null,
    row.status || 'active',
    now,
    now,
  );
  return Number(result.lastInsertRowid);
}

function getImageAssetForSpecies(speciesId) {
  return openDb().prepare(`
    SELECT * FROM fishit_global_image_assets
    WHERE species_id = ? AND status = 'active'
    ORDER BY id DESC LIMIT 1
  `).get(speciesId) || null;
}

function upsertItemMapping(row) {
  const db = openDb();
  const now = _now();
  const itemId = String(row.item_id || '').trim();
  if (!itemId) return null;
  const existing = db.prepare('SELECT * FROM fishit_global_item_mappings WHERE item_id = ?').get(itemId);
  if (existing) {
    db.prepare(`
      UPDATE fishit_global_item_mappings SET
        species_id = COALESCE(?, species_id),
        canonical_name = COALESCE(?, canonical_name),
        confidence = COALESCE(?, confidence),
        source = COALESCE(?, source),
        evidence_count = COALESCE(?, evidence_count),
        unique_user_count = COALESCE(?, unique_user_count),
        conflict_status = ?,
        game_id = COALESCE(?, game_id),
        place_id = COALESCE(?, place_id),
        last_seen_at = ?,
        updated_at = ?
      WHERE item_id = ?
    `).run(
      row.species_id || null,
      row.canonical_name || null,
      row.confidence || null,
      row.source || null,
      row.evidence_count ?? null,
      row.unique_user_count ?? null,
      row.conflict_status ?? existing.conflict_status,
      row.game_id || null,
      row.place_id || null,
      row.last_seen_at || now,
      now,
      itemId,
    );
    return existing.id;
  }
  const result = db.prepare(`
    INSERT INTO fishit_global_item_mappings (
      game_id, place_id, item_id, species_id, canonical_name, confidence, source,
      evidence_count, unique_user_count, conflict_status, first_seen_at, last_seen_at,
      created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    row.game_id || null,
    row.place_id || null,
    itemId,
    row.species_id || null,
    row.canonical_name || null,
    row.confidence || VERIFICATION.LIVE_OBSERVED,
    row.source || 'live_observed',
    row.evidence_count || 0,
    row.unique_user_count || 0,
    row.conflict_status || null,
    row.first_seen_at || now,
    row.last_seen_at || now,
    now,
    now,
  );
  return Number(result.lastInsertRowid);
}

function getItemMapping(itemId) {
  const id = String(itemId || '').trim();
  if (!id) return null;
  return openDb().prepare('SELECT * FROM fishit_global_item_mappings WHERE item_id = ?').get(id) || null;
}

function insertObservation(row) {
  const now = _now();
  const result = openDb().prepare(`
    INSERT INTO fishit_global_observations (
      anonymized_user_hash, session_key_hash, game_id, place_id, item_id,
      raw_name, parsed_base_name, mutation, weight_kg, rarity, source_payload_type,
      observed_at, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    row.anonymized_user_hash || null,
    row.session_key_hash || null,
    row.game_id || null,
    row.place_id || null,
    row.item_id || null,
    row.raw_name || null,
    row.parsed_base_name || null,
    row.mutation || null,
    row.weight_kg ?? null,
    row.rarity || null,
    row.source_payload_type || null,
    row.observed_at || now,
    now,
  );
  return Number(result.lastInsertRowid);
}

function upsertConflict(row) {
  const db = openDb();
  const now = _now();
  const existing = db.prepare(
    'SELECT id FROM fishit_global_conflicts WHERE item_id = ? AND conflict_type = ? AND status = ?',
  ).get(row.item_id, row.conflict_type, row.status || 'open');
  const payload = JSON.stringify(row.candidate_names || []);
  const speciesPayload = JSON.stringify(row.candidate_species_ids || []);
  const summary = JSON.stringify(row.evidence_summary || {});
  if (existing) {
    db.prepare(`
      UPDATE fishit_global_conflicts SET
        candidate_names = ?, candidate_species_ids = ?, evidence_summary = ?, updated_at = ?
      WHERE id = ?
    `).run(payload, speciesPayload, summary, now, existing.id);
    return existing.id;
  }
  const result = db.prepare(`
    INSERT INTO fishit_global_conflicts (
      conflict_type, game_id, place_id, item_id, candidate_names, candidate_species_ids,
      evidence_summary, status, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    row.conflict_type,
    row.game_id || null,
    row.place_id || null,
    row.item_id,
    payload,
    speciesPayload,
    summary,
    row.status || 'open',
    now,
    now,
  );
  return Number(result.lastInsertRowid);
}

function getStats() {
  const db = openDb();
  const lastObs = db.prepare(
    'SELECT observed_at FROM fishit_global_observations ORDER BY observed_at DESC LIMIT 1',
  ).get();
  return {
    dbPath: dbPath(),
    speciesCount: db.prepare('SELECT COUNT(*) AS c FROM fishit_global_species').get().c,
    imageAssetCount: db.prepare('SELECT COUNT(*) AS c FROM fishit_global_image_assets').get().c,
    mappingCount: db.prepare('SELECT COUNT(*) AS c FROM fishit_global_item_mappings').get().c,
    observationCount: db.prepare('SELECT COUNT(*) AS c FROM fishit_global_observations').get().c,
    openConflictCount: db.prepare("SELECT COUNT(*) AS c FROM fishit_global_conflicts WHERE status = 'open'").get().c,
    seedImportedCount: db.prepare(
      "SELECT COUNT(*) AS c FROM fishit_global_species WHERE verification_status = 'seed_imported'",
    ).get().c,
    manualVerifiedCount: db.prepare(
      "SELECT COUNT(*) AS c FROM fishit_global_species WHERE verification_status = 'manual_verified'",
    ).get().c,
    lastObservationAt: lastObs?.observed_at || null,
  };
}

function listSpecies(limit = 50, offset = 0) {
  return openDb().prepare(
    'SELECT * FROM fishit_global_species ORDER BY canonical_name LIMIT ? OFFSET ?',
  ).all(limit, offset);
}

function listConflicts(limit = 50) {
  const rows = openDb().prepare(
    "SELECT * FROM fishit_global_conflicts WHERE status = 'open' ORDER BY updated_at DESC LIMIT ?",
  ).all(limit);
  return rows.map((r) => ({
    ...r,
    candidate_names: JSON.parse(r.candidate_names || '[]'),
    candidate_species_ids: JSON.parse(r.candidate_species_ids || '[]'),
    evidence_summary: JSON.parse(r.evidence_summary || '{}'),
  }));
}

function listMappings(limit = 50) {
  return openDb().prepare(
    'SELECT * FROM fishit_global_item_mappings ORDER BY last_seen_at DESC LIMIT ?',
  ).all(limit);
}

function listUnresolvedSpecies(limit = 50) {
  return openDb().prepare(`
    SELECT * FROM fishit_global_species
    WHERE cached_image_url IS NULL OR rarity IS NULL OR verification_status = 'live_observed'
    ORDER BY updated_at DESC LIMIT ?
  `).all(limit);
}

function updateSpeciesRarity(speciesId, rarity, raritySource = 'manual_verified') {
  const db = openDb();
  const now = _now();
  db.prepare(`
    UPDATE fishit_global_species SET
      rarity = ?, rarity_source = ?, rarity_confidence = 'manual_verified', updated_at = ?
    WHERE id = ?
  `).run(rarity, raritySource, now, speciesId);
}

function setSpeciesManualVerified(speciesId, patch = {}) {
  const db = openDb();
  const now = _now();
  db.prepare(`
    UPDATE fishit_global_species SET
      canonical_name = COALESCE(?, canonical_name),
      rarity = COALESCE(?, rarity),
      rarity_source = COALESCE(?, rarity_source),
      verification_status = 'manual_verified',
      updated_at = ?
    WHERE id = ?
  `).run(patch.canonical_name || null, patch.rarity || null, patch.rarity_source || 'manual_verified', now, speciesId);
}

function quarantineMapping(itemId, reason) {
  const now = _now();
  openDb().prepare(`
    UPDATE fishit_global_item_mappings SET
      conflict_status = 'quarantined',
      confidence = 'quarantined_conflict',
      updated_at = ?
    WHERE item_id = ?
  `).run(now, String(itemId));
  upsertConflict({
    conflict_type: 'item_mapping',
    item_id: String(itemId),
    candidate_names: [reason],
    status: 'open',
  });
}

function clearLearnedData(options = {}) {
  const db = openDb();
  const preserveManual = options.preserveManualVerified !== false;
  if (preserveManual) {
    db.prepare(`
      DELETE FROM fishit_global_item_mappings
      WHERE confidence NOT IN ('manual_verified', 'seed_imported')
         OR conflict_status = 'quarantined'
    `).run();
    db.prepare(`
      DELETE FROM fishit_global_observations
    `).run();
    db.prepare(`
      DELETE FROM fishit_global_conflicts
    `).run();
    db.prepare(`
      UPDATE fishit_global_species SET
        verification_status = 'seed_imported',
        updated_at = ?
      WHERE verification_status IN ('live_observed', 'multi_user_confirmed', 'quarantined_conflict')
    `).run(_now());
  } else {
    db.exec(`
      DELETE FROM fishit_global_image_assets;
      DELETE FROM fishit_global_item_mappings;
      DELETE FROM fishit_global_observations;
      DELETE FROM fishit_global_conflicts;
      DELETE FROM fishit_global_species;
    `);
  }
  return getStats();
}

function quarantineItemIds(itemIds, reason) {
  const db = openDb();
  const now = _now();
  for (const raw of itemIds || []) {
    const itemId = String(raw).trim();
    if (!itemId) continue;
    const existing = db.prepare('SELECT * FROM fishit_global_item_mappings WHERE item_id = ?').get(itemId);
    if (existing) {
      quarantineMapping(itemId, reason);
    } else {
      upsertItemMapping({
        item_id: itemId,
        canonical_name: null,
        confidence: VERIFICATION.QUARANTINED_CONFLICT,
        source: 'reset_quarantine',
        conflict_status: 'quarantined',
        evidence_count: 0,
        unique_user_count: 0,
      });
      db.prepare(`
        UPDATE fishit_global_item_mappings SET conflict_status = 'quarantined' WHERE item_id = ?
      `).run(itemId);
    }
    upsertConflict({
      conflict_type: 'reset_quarantine',
      item_id: itemId,
      candidate_names: [reason],
      status: 'open',
    });
  }
}

function _reset() {
  if (_db) {
    try { _db.close(); } catch (_) { /* */ }
  }
  _db = null;
  _dbPath = null;
  const p = dbPath();
  if (process.env.NODE_ENV === 'test' && fs.existsSync(p)) {
    try { fs.unlinkSync(p); } catch (_) { /* */ }
    try { fs.unlinkSync(`${p}-wal`); } catch (_) { /* */ }
    try { fs.unlinkSync(`${p}-shm`); } catch (_) { /* */ }
  }
}

module.exports = {
  dbPath,
  get DB_PATH() { return dbPath(); },
  VERIFICATION,
  openDb,
  closeDb,
  normalizeName,
  normalizeNamePunct,
  hashContributor,
  upsertSpecies,
  getSpeciesById,
  getSpeciesByNormalizedName,
  findSpeciesByAliases,
  upsertImageAsset,
  getImageAssetForSpecies,
  upsertItemMapping,
  getItemMapping,
  insertObservation,
  upsertConflict,
  getStats,
  listSpecies,
  listConflicts,
  listMappings,
  listUnresolvedSpecies,
  updateSpeciesRarity,
  setSpeciesManualVerified,
  quarantineMapping,
  clearLearnedData,
  quarantineItemIds,
  _reset,
};
