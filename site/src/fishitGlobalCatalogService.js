'use strict';
/**
 * BLOCKER10V — global collective catalog service (SQLite source of truth).
 */

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const globalDb = require('./fishitGlobalDb');
const catchNameParser = require('./fishitCatchNameParser');
const protectedFishNames = require('./fishitProtectedFishNames');
const quizBotCatalog = require('./fishitQuizBotImageCatalog');

const SOURCE_GLOBAL = 'global_db';
const SEED_SOURCE_QUIZ = 'quiz_bot_import';

/** Known species rarities (game-verified seeds, not guessed from color/name). */
const SPECIES_RARITY_SEED = {
  'freshwater piranha': { rarity: 'Rare', source: 'game_verified_seed' },
  'giant squid': { rarity: 'Secret', source: 'manual_verified_catalog' },
  'panther eel': { rarity: 'Secret', source: 'manual_verified_catalog' },
};

const CONFIDENCE_RANK = {
  manual_verified: 100,
  seed_imported: 80,
  multi_user_confirmed: 60,
  live_observed: 30,
  quarantined_conflict: 0,
};

let _lastImportResult = null;

/** Per-session observation rate limit (anti-poisoning). */
const _obsRate = new Map();
const OBS_RATE_WINDOW_MS = 60 * 1000;

function _obsRateMax() {
  return Number(process.env.FISHIT_GLOBAL_OBS_RATE_MAX) || 120;
}

function _checkObservationRateLimit(sessionHash) {
  if (process.env.NODE_ENV === 'test' && !process.env.FISHIT_GLOBAL_OBS_RATE_MAX) return true;
  const max = _obsRateMax();
  const key = sessionHash || 'anon';
  const now = Date.now();
  let entry = _obsRate.get(key);
  if (!entry || now - entry.windowStart > OBS_RATE_WINDOW_MS) {
    entry = { count: 0, windowStart: now };
    _obsRate.set(key, entry);
  }
  entry.count += 1;
  return entry.count <= max;
}

function collectAliases(itemOrName) {
  if (typeof itemOrName === 'string') return [String(itemOrName).trim()].filter(Boolean);
  return quizBotCatalog.collectAliases(itemOrName);
}

function resolveSpeciesForItem(item) {
  const aliases = collectAliases(item);
  const itemId = item?.itemId ? String(item.itemId).trim() : null;

  if (itemId) {
    const mapping = globalDb.getItemMapping(itemId);
    if (mapping && mapping.conflict_status !== 'quarantined' && mapping.species_id) {
      const sp = globalDb.getSpeciesById(mapping.species_id);
      if (sp && sp.verification_status !== globalDb.VERIFICATION.QUARANTINED_CONFLICT) {
        return {
          species: sp,
          mapping,
          matchedAlias: sp.canonical_name,
          triedAliases: aliases,
          resolveSource: 'global_item_mapping',
        };
      }
    }
  }

  const hit = globalDb.findSpeciesByAliases(aliases);
  if (hit) {
    return {
      species: hit.species,
      mapping: itemId ? globalDb.getItemMapping(itemId) : null,
      matchedAlias: hit.matchedAlias,
      triedAliases: aliases,
      resolveSource: 'global_species_name',
    };
  }

  return {
    species: null,
    mapping: itemId ? globalDb.getItemMapping(itemId) : null,
    matchedAlias: null,
    triedAliases: aliases,
    resolveSource: null,
  };
}

function resolveImageForItem(item) {
  const resolved = resolveSpeciesForItem(item);
  if (!resolved.species) return { ...resolved, image: null };

  const sp = resolved.species;
  const asset = globalDb.getImageAssetForSpecies(sp.id);
  return {
    ...resolved,
    image: {
      speciesId: sp.id,
      canonicalName: sp.canonical_name,
      imageSource: SOURCE_GLOBAL,
      seedSource: sp.source === SEED_SOURCE_QUIZ ? SEED_SOURCE_QUIZ : sp.source,
      cachedUrl: asset?.local_cached_url || sp.cached_image_url || null,
      contentHash: asset?.content_hash || null,
      originalSource: asset?.original_source || sp.image_source,
      originalPath: asset?.original_url_or_path || null,
      quizBotBankId: sp.quiz_bot_bank_id,
      quizBotAssetId: sp.quiz_bot_asset_id,
      matchedAliases: resolved.triedAliases,
      matchedAlias: resolved.matchedAlias,
      localFilePath: null,
    },
  };
}

function resolveRarityForItem(item) {
  const resolved = resolveSpeciesForItem(item);
  if (!resolved.species) return { ...resolved, rarity: null };

  const sp = resolved.species;
  if (!sp.rarity) return { ...resolved, rarity: null };

  return {
    ...resolved,
    rarity: {
      rarity: sp.rarity,
      raritySource: sp.rarity_source || SOURCE_GLOBAL,
      rarityConfidence: sp.rarity_confidence || sp.verification_status,
      speciesId: sp.id,
    },
  };
}

function _confidenceRank(status) {
  return CONFIDENCE_RANK[status] ?? 10;
}

function _catalogMetaFromMapping(mapping) {
  if (!mapping || !mapping.canonical_name) return null;
  if (mapping.conflict_status === 'quarantined') return null;
  const sp = mapping.species_id ? globalDb.getSpeciesById(mapping.species_id) : null;
  const baseName = mapping.canonical_name;
  return {
    name: baseName,
    displayName: sp?.display_name || baseName,
    baseFishName: baseName,
    mutation: null,
    category: 'fish',
    source: 'global_db',
    globalDbSource: mapping.source || SOURCE_GLOBAL,
    tier: sp?.rarity || null,
    imageUrl: sp?.cached_image_url || null,
    imageAssetId: sp?.quiz_bot_asset_id || null,
    confidence: mapping.confidence,
    publicEligible: true,
    speciesId: sp?.id || mapping.species_id || null,
    evidenceCount: mapping.evidence_count,
    uniqueUserCount: mapping.unique_user_count,
  };
}

/**
 * Resolve catalog meta for an itemId from global DB mappings.
 * @param {string} itemId
 * @param {{ allowLiveObserved?: boolean }} options
 */
function resolveCatalogMetaForItemId(itemId, options = {}) {
  const id = String(itemId || '').trim();
  if (!id) return null;
  const mapping = globalDb.getItemMapping(id);
  if (!mapping) return null;
  const rank = _confidenceRank(mapping.confidence);
  const allowLive = options.allowLiveObserved === true;
  const strong = rank >= CONFIDENCE_RANK.seed_imported;
  const multiUser = rank >= CONFIDENCE_RANK.multi_user_confirmed;
  const liveOnly = rank === CONFIDENCE_RANK.live_observed && allowLive;
  if (!strong && !multiUser && !liveOnly) return null;
  return _catalogMetaFromMapping(mapping);
}

function recordObservation(raw) {
  const itemId = raw?.itemId ? String(raw.itemId).trim() : null;
  const sessionHash = globalDb.hashContributor(raw.sessionKey, 'fishit_session_v1');
  if (!_checkObservationRateLimit(sessionHash)) {
    return { accepted: false, reason: 'rate_limited' };
  }
  const parsed = catchNameParser.parseCatchInput({
    fishName: raw.rawName || raw.name || raw.baseFishName,
    rawText: raw.rawName || raw.name,
    rarityCandidate: raw.rarity,
  });
  let baseName = parsed.baseFishName || parsed.fishNameCandidate || raw.baseFishName || raw.name;
  if (baseName && protectedFishNames.isProtectedBaseName(baseName)) {
    baseName = protectedFishNames.normalizeProtected(baseName);
  }

  const userHash = globalDb.hashContributor(raw.userId || raw.userIdHash);
  const now = new Date().toISOString();

  globalDb.insertObservation({
    anonymized_user_hash: userHash,
    session_key_hash: sessionHash,
    game_id: raw.gameId || null,
    place_id: raw.placeId || null,
    item_id: itemId,
    raw_name: raw.rawName || raw.name || null,
    parsed_base_name: baseName,
    mutation: raw.mutation || parsed.mutation || null,
    weight_kg: raw.weightKg ?? raw.weight ?? parsed.weightKg ?? null,
    rarity: raw.rarity || parsed.rarityCandidate || null,
    source_payload_type: raw.sourcePayloadType || raw.source || 'inventory_snapshot',
    observed_at: raw.observedAt || now,
  });

  if (!itemId || !baseName) {
    return { accepted: false, reason: 'missing_item_or_name' };
  }

  const speciesHit = globalDb.findSpeciesByAliases([baseName]);
  const speciesId = speciesHit?.species?.id || null;
  const existing = globalDb.getItemMapping(itemId);
  const incomingNorm = globalDb.normalizeNamePunct(baseName);

  if (existing && existing.canonical_name) {
    const existingNorm = globalDb.normalizeNamePunct(existing.canonical_name);
    if (existingNorm && incomingNorm && existingNorm !== incomingNorm) {
      globalDb.upsertConflict({
        conflict_type: 'item_id_name_mismatch',
        item_id: itemId,
        game_id: raw.gameId,
        place_id: raw.placeId,
        candidate_names: [existing.canonical_name, baseName],
        candidate_species_ids: [existing.species_id, speciesId].filter(Boolean),
        evidence_summary: { existingConfidence: existing.confidence, incoming: baseName },
      });
      const existingRank = _confidenceRank(existing.confidence);
      if (existingRank >= _confidenceRank(globalDb.VERIFICATION.SEED_IMPORTED)) {
        return { accepted: false, reason: 'conflict_blocked_by_stronger_source', itemId };
      }
      globalDb.quarantineMapping(itemId, `conflict:${existing.canonical_name} vs ${baseName}`);
      return { accepted: true, decision: 'quarantined', itemId, conflict: true };
    }
  }

  const evidenceCount = (existing?.evidence_count || 0) + 1;
  const uniqueUsers = new Set();
  if (existing?.unique_user_count) uniqueUsers.add('existing');
  if (userHash) uniqueUsers.add(userHash);
  const uniqueCount = userHash ? Math.max(existing?.unique_user_count || 0, uniqueUsers.size) : (existing?.unique_user_count || 1);

  let confidence = globalDb.VERIFICATION.LIVE_OBSERVED;
  if (uniqueCount >= 2 || evidenceCount >= 3) {
    confidence = globalDb.VERIFICATION.MULTI_USER_CONFIRMED;
  }
  let source = 'live_observed';
  if (existing && _confidenceRank(existing.confidence) >= _confidenceRank(globalDb.VERIFICATION.MANUAL_VERIFIED)) {
    confidence = existing.confidence;
    source = existing.source || source;
  }

  globalDb.upsertItemMapping({
    item_id: itemId,
    species_id: speciesId,
    canonical_name: baseName,
    confidence,
    source,
    evidence_count: evidenceCount,
    unique_user_count: uniqueCount,
    game_id: raw.gameId,
    place_id: raw.placeId,
    last_seen_at: now,
    conflict_status: null,
  });

  return {
    accepted: true,
    itemId,
    baseName,
    speciesId,
    confidence,
    evidenceCount,
    uniqueUserCount: uniqueCount,
    userHash,
    sessionHash,
  };
}

async function importQuizBotSeed(options = {}) {
  const fishImageCache = require('./fishitFishImageCache');
  const bankPath = options.bankPath || quizBotCatalog.BANK_PATH;
  const assetsDir = options.assetsDir || quizBotCatalog.ASSETS_DIR;

  if (!fs.existsSync(bankPath)) {
    const err = { ok: false, reason: 'bank_not_found', bankPath };
    _lastImportResult = err;
    return err;
  }

  const rows = JSON.parse(fs.readFileSync(bankPath, 'utf8'));
  const arr = Array.isArray(rows) ? rows : [];
  let speciesImported = 0;
  let imagesImported = 0;
  let skipped = 0;
  const proof = [];

  for (const entry of arr) {
    if (!entry?.name) { skipped += 1; continue; }
    const canonical = String(entry.name).trim();
    const normalized = globalDb.normalizeNamePunct(canonical);
    const raritySeed = SPECIES_RARITY_SEED[normalized] || null;
    const localFile = entry.localFile ? path.join(assetsDir, entry.localFile) : null;
    const hasLocal = localFile && fs.existsSync(localFile);

    const speciesId = globalDb.upsertSpecies({
      normalized_name: normalized,
      canonical_name: canonical,
      display_name: canonical,
      rarity: raritySeed?.rarity || null,
      rarity_source: raritySeed?.source || null,
      rarity_confidence: raritySeed ? globalDb.VERIFICATION.SEED_IMPORTED : null,
      quiz_bot_bank_id: entry.id || null,
      quiz_bot_asset_id: entry.assetId ? String(entry.assetId) : null,
      image_source: hasLocal ? SOURCE_GLOBAL : null,
      source: SEED_SOURCE_QUIZ,
      verification_status: globalDb.VERIFICATION.SEED_IMPORTED,
    });
    speciesImported += 1;

    let cachedUrl = null;
    let contentHash = null;
    if (hasLocal) {
      const cached = await fishImageCache.ensureCachedFromLocalFile(localFile, {
        baseFishName: canonical,
      });
      cachedUrl = cached?.localUrl || null;
      contentHash = cached?.sha256 || cached?.localFile || null;
      if (cachedUrl) {
        globalDb.upsertImageAsset({
          species_id: speciesId,
          canonical_name: canonical,
          original_source: SEED_SOURCE_QUIZ,
          original_url_or_path: localFile,
          local_cached_url: cachedUrl,
          content_hash: contentHash,
          mime_type: cached?.mimeType || 'image/webp',
          status: 'active',
        });
        globalDb.upsertSpecies({
          normalized_name: normalized,
          canonical_name: canonical,
          cached_image_url: cachedUrl,
          image_source: SOURCE_GLOBAL,
        });
        imagesImported += 1;
      }
    }

    if (proof.length < 15) {
      proof.push({
        canonicalName: canonical,
        speciesId,
        quizBotBankId: entry.id,
        quizBotAssetId: entry.assetId,
        localFile: entry.localFile,
        cachedUrl,
        contentHash,
        rarity: raritySeed?.rarity || null,
      });
    }
  }

  const manual = require('./fishitManualVerifiedCatalog');
  for (const row of manual.getAll()) {
    if (!row.baseFishName) continue;
    const normalized = globalDb.normalizeNamePunct(row.baseFishName);
    globalDb.upsertSpecies({
      normalized_name: normalized,
      canonical_name: row.baseFishName,
      rarity: row.rarity || 'Secret',
      rarity_source: 'manual_verified_catalog',
      rarity_confidence: globalDb.VERIFICATION.MANUAL_VERIFIED,
      verification_status: globalDb.VERIFICATION.MANUAL_VERIFIED,
      source: 'manual_verified_catalog',
    });
    if (row.itemId) {
      const sp = globalDb.getSpeciesByNormalizedName(row.baseFishName);
      globalDb.upsertItemMapping({
        item_id: String(row.itemId),
        species_id: sp?.id || null,
        canonical_name: row.baseFishName,
        confidence: globalDb.VERIFICATION.MANUAL_VERIFIED,
        source: 'manual_verified_catalog',
        evidence_count: 1,
        unique_user_count: 1,
      });
    }
  }

  const result = {
    ok: true,
    speciesImported,
    imagesImported,
    skipped,
    totalBankRows: arr.length,
    seedSource: SEED_SOURCE_QUIZ,
    bankPath,
    assetsDir,
    proof,
    stats: globalDb.getStats(),
  };
  _lastImportResult = result;
  return result;
}

function getLastImportResult() {
  return _lastImportResult;
}

function buildGlobalCatalogProof(items, limit = 15) {
  return (items || []).slice(0, limit).map((item) => {
    const r = resolveSpeciesForItem(item);
    return {
      itemId: item.itemId || null,
      baseFishName: item.baseFishName || item.name,
      speciesId: r.species?.id || null,
      canonicalName: r.species?.canonical_name || null,
      verificationStatus: r.species?.verification_status || null,
      resolveSource: r.resolveSource,
      matchedAlias: r.matchedAlias,
      triedAliases: r.triedAliases,
    };
  });
}

function buildGlobalDbSummaryProof() {
  const stats = globalDb.getStats();
  const last = _lastImportResult || {};
  return {
    enabled: true,
    sourceOfTruth: 'global_db',
    backend: 'sqlite',
    dbPath: stats.dbPath,
    speciesCount: stats.speciesCount,
    itemMappingCount: stats.mappingCount,
    observationCount: stats.observationCount,
    conflictCount: stats.openConflictCount,
    imageAssetCount: stats.imageAssetCount,
    seedImportedCount: stats.seedImportedCount,
    manualVerifiedCount: stats.manualVerifiedCount,
    lastImportAt: last.ok ? new Date().toISOString() : null,
    lastObservationAt: stats.lastObservationAt || null,
  };
}

function buildGlobalContributionProof(options = {}) {
  const stats = globalDb.getStats();
  return {
    acceptsUserEvidence: true,
    anonymizedUserHashUsed: true,
    rawIdentityExposed: false,
    promotionRules: [
      'manual_verified > quiz_bot_seed > multi_user_confirmed > live_observed',
      'single weak observation cannot override seed or manual_verified mapping',
      'conflicting itemId/name creates quarantine conflict row',
      '2+ unique users or 3+ evidence promotes to multi_user_confirmed',
    ],
    recentObservationAccepted: options.observationsWrittenThisUpload || 0,
    exampleFlow: 'User A observes itemId/name; global DB records anonymized evidence; after seed/manual/multi-user confirmation, User B resolves the same species automatically.',
    observationsWrittenThisUpload: options.observationsWrittenThisUpload || 0,
    anonymizedUserHash: options.anonymizedUserHash || null,
    uniqueUserCountUpdates: options.uniqueUserCountUpdates || 0,
    promotedMappings: options.promotedMappings || 0,
    quarantinedConflicts: options.quarantinedConflicts || stats.openConflictCount,
    blockedPoisonAttempts: options.blockedPoisonAttempts || 0,
    observationCount: stats.observationCount,
    mappingCount: stats.mappingCount,
  };
}

function buildGlobalImageProof(items, limit = 15) {
  return (items || []).slice(0, limit).map((item) => {
    const r = resolveImageForItem(item);
    const img = r.image;
    return {
      itemId: item.itemId || null,
      baseFishName: item.baseFishName || item.name,
      speciesId: img?.speciesId || null,
      source: img?.imageSource || null,
      seedSource: img?.seedSource || null,
      cachedUrl: img?.cachedUrl || item.imageUrl || null,
      contentHash: img?.contentHash || null,
      matchedAliases: img?.matchedAliases || r.triedAliases,
      matchedAlias: img?.matchedAlias || null,
      quizBotBankId: img?.quizBotBankId || null,
    };
  });
}

function buildGlobalRarityProof(items, limit = 15) {
  return (items || []).slice(0, limit).map((item) => {
    const r = resolveRarityForItem(item);
    return {
      itemId: item.itemId || null,
      baseFishName: item.baseFishName || item.name,
      speciesId: r.rarity?.speciesId || r.species?.id || null,
      rarity: r.rarity?.rarity || item.rarity || null,
      raritySource: r.rarity?.raritySource || null,
      rarityConfidence: r.rarity?.rarityConfidence || null,
    };
  });
}

function buildGlobalEvidenceProof(limit = 15) {
  const db = globalDb.openDb();
  const rows = db.prepare(`
    SELECT item_id, canonical_name, confidence, evidence_count, unique_user_count, conflict_status, last_seen_at
    FROM fishit_global_item_mappings
    ORDER BY last_seen_at DESC LIMIT ?
  `).all(limit);
  return {
    recentMappings: rows,
    observationCount: globalDb.getStats().observationCount,
  };
}

function buildGlobalConflictProof(limit = 15) {
  return globalDb.listConflicts(limit);
}

function buildQuizBotSeedImportProof() {
  const last = _lastImportResult || {};
  return {
    lastImport: last.ok ? {
      speciesImported: last.speciesImported,
      imagesImported: last.imagesImported,
      totalBankRows: last.totalBankRows,
      seedSource: last.seedSource,
      bankPath: last.bankPath,
    } : null,
    stats: globalDb.getStats(),
    sampleProof: last.proof || [],
  };
}

function buildGlobalDbUiProof(publicItems) {
  const stats = globalDb.getStats();
  const items = publicItems || [];
  const globalImages = items.filter((i) => i.imageSource === 'global_db' && i.imageUrl).length;
  const globalRarity = items.filter((i) => i.raritySource && (
    i.raritySource === 'global_db'
    || i.raritySource === 'manual_verified'
    || i.raritySource === 'ui_name_color'
    || String(i.raritySource).includes('seed')
    || String(i.raritySource).includes('canonical')
  )).length;
  return {
    enabled: true,
    sourceOfTruth: 'global_db',
    backend: 'sqlite',
    speciesCount: stats.speciesCount,
    mappingCount: stats.mappingCount,
    observationCount: stats.observationCount,
    imageAssetCount: stats.imageAssetCount,
    cardsUsingGlobalDbImages: globalImages,
    cardsUsingGlobalDbRarity: globalRarity,
    cardsTotal: items.length,
    cardsUsingGlobalDbImagesLabel: `${globalImages}/${items.length}`,
    cardsUsingGlobalDbRarityLabel: `${globalRarity}/${items.length}`,
  };
}

function _reset() {
  globalDb._reset();
  _lastImportResult = null;
  _obsRate.clear();
}

/**
 * Admin-approved manual itemId → species mapping (BLOCKER10X).
 * @param {{ itemId: string|number, canonicalName: string, source?: string, verificationStatus?: string, reason?: string }} body
 */
function approveItemMapping(body) {
  const itemId = String(body?.itemId || '').trim();
  const canonicalName = String(body?.canonicalName || '').trim();
  if (!itemId || !canonicalName) {
    return { ok: false, error: 'invalid_input' };
  }

  const hit = globalDb.findSpeciesByAliases([canonicalName]);
  if (!hit?.species) {
    return { ok: false, error: 'species_not_found', canonicalName };
  }

  const existing = globalDb.getItemMapping(itemId);
  const evidenceCount = (existing?.evidence_count || 0) + 1;
  const conflictStatus = body.verificationStatus === 'manual_verified'
    ? 'resolved'
    : (body.conflict_status || 'manual_verified');

  globalDb.upsertItemMapping({
    item_id: itemId,
    species_id: hit.species.id,
    canonical_name: canonicalName,
    confidence: globalDb.VERIFICATION.MANUAL_VERIFIED,
    source: body.source || 'admin_manual_screenshot_confirmation',
    evidence_count: evidenceCount,
    unique_user_count: Math.max(existing?.unique_user_count || 0, 1),
    conflict_status: conflictStatus,
  });

  const mapping = globalDb.getItemMapping(itemId);
  return {
    ok: true,
    itemId,
    mappingId: mapping?.id || null,
    speciesId: hit.species.id,
    quizBotBankId: hit.species.quiz_bot_bank_id || null,
    canonicalName,
    confidence: globalDb.VERIFICATION.MANUAL_VERIFIED,
    source: body.source || 'admin_manual_screenshot_confirmation',
    reason: body.reason || null,
    species: {
      id: hit.species.id,
      canonical_name: hit.species.canonical_name,
      quiz_bot_bank_id: hit.species.quiz_bot_bank_id,
      cached_image_url: hit.species.cached_image_url,
    },
  };
}

module.exports = {
  SOURCE_GLOBAL,
  SEED_SOURCE_QUIZ,
  SPECIES_RARITY_SEED,
  collectAliases,
  resolveSpeciesForItem,
  resolveImageForItem,
  resolveRarityForItem,
  resolveCatalogMetaForItemId,
  recordObservation,
  importQuizBotSeed,
  getLastImportResult,
  buildGlobalCatalogProof,
  buildGlobalDbSummaryProof,
  buildGlobalImageProof,
  buildGlobalRarityProof,
  buildGlobalEvidenceProof,
  buildGlobalConflictProof,
  buildGlobalContributionProof,
  buildQuizBotSeedImportProof,
  buildGlobalDbUiProof,
  approveItemMapping,
  _reset,
};
