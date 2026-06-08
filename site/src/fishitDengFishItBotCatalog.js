'use strict';
/**
 * BLOCKER10Z11 — DENG Fish It bot rarity/name authority (Secret + Forgotten).
 *
 * Source: deng-fish-it.sqlite app_kv (forgotten_fish catalog + alltime_fish_cache).
 * Bot does NOT track Rare/Epic/Legendary tiers — those come from game_verified_seed.
 */

const path = require('path');
const catchNameParser = require('./fishitCatchNameParser');
const protectedFishNames = require('./fishitProtectedFishNames');

let fishitDb = null;
try { fishitDb = require('./fishitDb'); } catch (_) { fishitDb = null; }

const SOURCE_ID = 'deng_fish_it_bot';
const SOURCE_TYPE = 'deng_fish_it_bot_sqlite';
const DEFAULT_DB_PATH = fishitDb?.DB_PATH || path.join(
  __dirname, '..', '..', '..', 'DENG Fish It', 'data', 'deng-fish-it.sqlite',
);

/** Authoritative Forgotten catalog key (admin-maintained in bot). */
const KEY_FORGOTTEN = 'forgotten_fish';
/** Per-user catch buckets with secretFish / forgottenFish maps. */
const KEY_FISH = 'alltime_fish_cache';

const RARITY_RANK = { Forgotten: 200, Secret: 100 };

let _byNorm = null;
let _conflicts = [];
let _loadMeta = null;

function normalizeKey(name) {
  return String(name || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function normalizePunct(name) {
  return normalizeKey(name)
    .replace(/[''`]/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function canonicalBaseName(raw) {
  const s = String(raw || '').trim();
  if (!s) return null;
  if (protectedFishNames.isProtectedBaseName(s)) {
    return protectedFishNames.normalizeProtected(s);
  }
  const parsed = catchNameParser.canonicalizeFishName(s);
  return parsed.baseFishName || s;
}

function _readBotBlob(key) {
  if (fishitDb && typeof fishitDb._readBlobForCatalog === 'function') {
    return fishitDb._readBlobForCatalog(key);
  }
  if (fishitDb && key === KEY_FORGOTTEN) {
    try {
      const hints = fishitDb.exportRarityHints?.() || [];
      const forgNames = hints.filter((h) => h.source === 'forgotten_fish_catalog').map((h) => h.name);
      if (forgNames.length) return { fish: forgNames.map((name) => ({ name })) };
    } catch (_) { /* */ }
  }
  return null;
}

function _loadFromFishitDb() {
  const map = new Map();
  const conflicts = [];
  const evidence = { forgottenCatalog: 0, forgottenBucket: 0, secretBucket: 0 };

  const addEntry = (rawName, rarity, sourceEvidence) => {
    const baseFishName = canonicalBaseName(rawName);
    if (!baseFishName) return;
    const norm = normalizePunct(baseFishName);
    if (!norm) return;
    const existing = map.get(norm);
    const rank = RARITY_RANK[rarity] || 0;
    const entry = {
      baseFishName,
      rarity,
      rarityDisplayName: rarity,
      raritySource: SOURCE_ID,
      rarityConfidence: sourceEvidence.confidence,
      sourceEvidence: sourceEvidence.detail,
      sourceType: SOURCE_TYPE,
      normalizedKey: norm,
    };
    if (!existing) {
      map.set(norm, entry);
      return;
    }
    const existingRank = RARITY_RANK[existing.rarity] || 0;
    if (rank > existingRank) {
      conflicts.push({
        baseFishName,
        kept: { rarity: existing.rarity, evidence: existing.sourceEvidence },
        dropped: { rarity, evidence: sourceEvidence.detail },
        resolution: 'forgotten_over_secret',
      });
      map.set(norm, entry);
    } else if (rank < existingRank) {
      conflicts.push({
        baseFishName,
        kept: { rarity: existing.rarity, evidence: existing.sourceEvidence },
        dropped: { rarity, evidence: sourceEvidence.detail },
        resolution: 'forgotten_over_secret',
      });
    } else if (existing.rarity !== rarity) {
      conflicts.push({
        baseFishName,
        kept: { rarity: existing.rarity, evidence: existing.sourceEvidence },
        dropped: { rarity, evidence: sourceEvidence.detail },
        resolution: 'quarantined_conflict',
      });
      map.set(norm, { ...existing, status: 'quarantined', conflicts: [existing.rarity, rarity] });
    }
  };

  if (fishitDb) {
    try {
      const hints = fishitDb.exportRarityHints();
      for (const h of hints) {
        if (!h?.name || !h?.rarity) continue;
        if (h.source === 'forgotten_fish_catalog') {
          evidence.forgottenCatalog += 1;
          addEntry(h.name, 'Forgotten', {
            confidence: 'forgotten_fish_catalog',
            detail: 'app_kv.forgotten_fish',
          });
        } else if (h.source === 'fishit_db_forgotten') {
          evidence.forgottenBucket += 1;
          addEntry(h.name, 'Forgotten', {
            confidence: 'bot_catch_bucket',
            detail: 'alltime_fish_cache.forgottenFish',
          });
        } else if (h.source === 'fishit_db_secret') {
          evidence.secretBucket += 1;
          addEntry(h.name, 'Secret', {
            confidence: 'bot_catch_bucket',
            detail: 'alltime_fish_cache.secretFish',
          });
        }
      }
    } catch (err) {
      console.warn('[fishit] deng bot catalog load failed:', err && err.message ? err.message : err);
    }
  }

  _byNorm = map;
  _conflicts = conflicts;
  _loadMeta = {
    sourcePath: fishitDb?.DB_PATH || DEFAULT_DB_PATH,
    sourceType: SOURCE_TYPE,
    rowsLoaded: map.size,
    evidence,
    loadedAt: new Date().toISOString(),
  };
}

function _ensureLoaded() {
  if (!_byNorm) _loadFromFishitDb();
}

function lookupEntry(nameOrItem) {
  _ensureLoaded();
  const raw = typeof nameOrItem === 'string'
    ? nameOrItem
    : (nameOrItem?.baseFishName || nameOrItem?.name || nameOrItem?.displayName);
  const base = canonicalBaseName(raw);
  if (!base) return null;
  const norm = normalizePunct(base);
  const hit = _byNorm.get(norm);
  if (!hit || hit.status === 'quarantined') return hit?.status === 'quarantined' ? hit : null;
  return { ...hit };
}

function lookupRarity(nameOrItem) {
  const hit = lookupEntry(nameOrItem);
  if (!hit || hit.status === 'quarantined') return null;
  return {
    rarity: hit.rarity,
    raritySource: hit.raritySource,
    rarityConfidence: hit.rarityConfidence,
    baseFishName: hit.baseFishName,
    sourceEvidence: hit.sourceEvidence,
  };
}

function getAllEntries() {
  _ensureLoaded();
  return [..._byNorm.values()].filter((e) => e.status !== 'quarantined');
}

function getQuarantinedConflicts() {
  _ensureLoaded();
  return _conflicts.slice();
}

function getRarityCounts() {
  _ensureLoaded();
  const counts = {};
  for (const e of _byNorm.values()) {
    if (e.status === 'quarantined') continue;
    counts[e.rarity] = (counts[e.rarity] || 0) + 1;
  }
  return counts;
}

function buildCatalogProof(publicFishNames = []) {
  _ensureLoaded();
  const names = Array.isArray(publicFishNames) ? publicFishNames : [];
  const missing = [];
  for (const n of names) {
    const base = canonicalBaseName(n);
    if (!base) continue;
    const norm = normalizePunct(base);
    if (!_byNorm.has(norm)) missing.push(base);
  }
  const sampleEntries = getAllEntries()
    .filter((e) => ['Giant Squid', 'Panther Eel', 'Thunderzilla', 'Freshwater Piranha', 'Radiant Catfish']
      .some((t) => normalizePunct(t) === e.normalizedKey))
    .slice(0, 12);
  if (sampleEntries.length < 8) {
    for (const e of getAllEntries().slice(0, 8)) {
      if (!sampleEntries.some((s) => s.normalizedKey === e.normalizedKey)) sampleEntries.push(e);
    }
  }
  return {
    sourcePath: _loadMeta?.sourcePath || DEFAULT_DB_PATH,
    sourceType: SOURCE_TYPE,
    sourceKeys: [KEY_FORGOTTEN, KEY_FISH],
    rowsLoaded: _loadMeta?.rowsLoaded || 0,
    rarityCounts: getRarityCounts(),
    sampleEntries: sampleEntries.map((e) => ({
      baseFishName: e.baseFishName,
      rarity: e.rarity,
      raritySource: e.raritySource,
      rarityConfidence: e.rarityConfidence,
      sourceEvidence: e.sourceEvidence,
    })),
    missingCurrentPublicFish: missing,
    quarantinedConflicts: _conflicts.slice(0, 20),
    evidenceBreakdown: _loadMeta?.evidence || null,
    botTierCoverage: {
      Secret: true,
      Forgotten: true,
      Rare: false,
      Epic: false,
      Legendary: false,
      note: 'Bot tracks Secret/Forgotten catch buckets only; game tiers use game_verified_seed.',
    },
  };
}

function getCatalogMeta() {
  _ensureLoaded();
  return {
    sourceId: SOURCE_ID,
    sourceType: SOURCE_TYPE,
    sourcePath: _loadMeta?.sourcePath || DEFAULT_DB_PATH,
    entryCount: _byNorm.size,
    rarityCounts: getRarityCounts(),
  };
}

function _reset() {
  _byNorm = null;
  _conflicts = [];
  _loadMeta = null;
  if (fishitDb?._resetCache) fishitDb._resetCache();
}

module.exports = {
  SOURCE_ID,
  SOURCE_TYPE,
  DEFAULT_DB_PATH,
  KEY_FORGOTTEN,
  KEY_FISH,
  canonicalBaseName,
  lookupEntry,
  lookupRarity,
  getAllEntries,
  getQuarantinedConflicts,
  getRarityCounts,
  buildCatalogProof,
  getCatalogMeta,
  _reset,
};
