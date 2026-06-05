'use strict';
/**
 * Global collective fish item catalog (BLOCKER10Q).
 * Shared itemId -> fishName mappings from safe catch-delta evidence across all users.
 * JSON-backed; no full inventory storage.
 */

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const rarityLabels = require('./fishitRarityLabels');
const nameOnlyCatalog = require('./fishitNameOnlyCatalog');
const fishImageAssets = require('./fishitFishImageAssets');
const catalogStore = require('./fishitCatalogStore');
const learnedFishCatalog = require('./fishitLearnedFishCatalog');

const RARITY_ALIASES = {
  common: 'Common', uncommon: 'Uncommon', rare: 'Rare', epic: 'Epic',
  legend: 'Legendary', legendary: 'Legendary', mythic: 'Mythic', secret: 'Secret',
  forgotten: 'Forgotten', limited: 'Limited', event: 'Event',
  shiny: 'Shiny', mutation: 'Mutation', divine: 'Divine', celestial: 'Celestial',
  exotic: 'Exotic', special: 'Special', normal: 'Normal',
};

function _resetMergedFishCatalog() {
  try { require('./fishitFishCatalog')._reset(); } catch (_) { /* optional */ }
}

function storePath() {
  return process.env.FISHIT_GLOBAL_FISH_ITEM_CATALOG_PATH
    || path.join(__dirname, '..', 'data', 'fishit_global_fish_item_catalog.json');
}

const VERIFIED_CATCH_SOURCES = new Set(['catch_notification', 'catch_event']);
const MAX_RECENT_EVENTS = 50;

const FORCE_BLOCKED = [
  { itemId: '196', learnedName: 'Forgotten', reason: 'name_is_rarity_label' },
];

let _store = null;
let _lastIngestResult = null;

function _defaultStore() {
  return { updatedAt: null, storage: 'json', byItemId: {}, recentEvents: [] };
}

function _maybePersist() {
  if (process.env.NODE_ENV !== 'test' || process.env.FISHIT_GLOBAL_PERSIST === '1') _persist();
}

function _persist() {
  const sp = storePath();
  const dir = path.dirname(sp);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(sp, JSON.stringify(_store, null, 2), 'utf8');
}

function _load() {
  if (_store) return _store;
  try {
    const sp = storePath();
    if (fs.existsSync(sp)) {
      const raw = JSON.parse(fs.readFileSync(sp, 'utf8'));
      _store = {
        updatedAt: raw.updatedAt || null,
        storage: raw.storage || 'json',
        byItemId: (raw.byItemId && typeof raw.byItemId === 'object') ? raw.byItemId : {},
        recentEvents: Array.isArray(raw.recentEvents) ? raw.recentEvents.slice(-MAX_RECENT_EVENTS) : [],
      };
      _purgeForceBlocked();
      return _store;
    }
  } catch (_) { /* fall through */ }
  _store = _defaultStore();
  _purgeForceBlocked();
  return _store;
}

function normalizeFishName(raw) {
  return fishImageAssets.normalizeNamePunct(raw) || fishImageAssets.normalizeName(raw);
}

function normalizeRarity(raw) {
  if (!raw) return null;
  const t = String(raw).trim().toLowerCase();
  if (!t || t === 'unknown' || t === '-') return null;
  if (Object.prototype.hasOwnProperty.call(RARITY_ALIASES, t)) return RARITY_ALIASES[t];
  const norm = catalogStore.normalizeTier(t);
  if (RARITY_ALIASES[norm] === undefined && norm) {
    return norm.charAt(0).toUpperCase() + norm.slice(1);
  }
  return RARITY_ALIASES[norm] || null;
}

function sanitiseItemId(raw) {
  const id = String(raw || '').trim();
  return /^\d+$/.test(id) ? id : null;
}

function hashContributorId(userId) {
  if (userId == null || userId === '') return null;
  const salt = process.env.FISHIT_GLOBAL_CATALOG_SALT || 'fishit_global_v1';
  return crypto.createHash('sha256').update(`${salt}:${userId}`).digest('hex').slice(0, 16);
}

function publicEligible(entry) {
  if (!entry) return false;
  if (entry.confidence !== 'confirmed') return false;
  if (entry.publicEligible !== true) return false;
  if (entry.blockedReason) return false;
  if (!entry.fishName || /^Item #\d+$/i.test(entry.fishName)) return false;
  if (rarityLabels.isBlockedLearnName(entry.fishName)) return false;
  return true;
}

function _pushRecentEvent(evt) {
  _store.recentEvents.push({
    timestamp: evt.timestamp || new Date().toISOString(),
    itemId: evt.itemId || null,
    decision: evt.decision || null,
    reason: evt.reason || null,
    fishName: evt.fishName || null,
    rarity: evt.rarity || null,
  });
  if (_store.recentEvents.length > MAX_RECENT_EVENTS) {
    _store.recentEvents = _store.recentEvents.slice(-MAX_RECENT_EVENTS);
  }
}

function _makeEntry(itemId, fishName, extra) {
  const now = new Date().toISOString();
  const nv = nameOnlyCatalog.validateFishName(fishName);
  const img = nv.imageAssetId ? { imageAssetId: nv.imageAssetId } : fishImageAssets.lookupByFishName(fishName);
  return {
    itemId,
    normalizedItemId: itemId,
    fishName,
    normalizedFishName: normalizeFishName(fishName),
    rarity: extra.rarity != null ? extra.rarity : null,
    normalizedRarity: extra.rarity != null ? normalizeRarity(extra.rarity) : null,
    imageUrl: nv.imageUrl || null,
    imageAssetId: nv.imageAssetId || (img && img.assetId) || null,
    confidence: extra.confidence || 'pending',
    publicEligible: false,
    evidenceCount: 0,
    uniqueUserCount: 0,
    source: extra.source || 'catch_delta',
    sources: extra.sources || ['catch_delta'],
    firstSeenAt: now,
    lastSeenAt: now,
    lastConfirmedAt: null,
    gameId: extra.gameId || null,
    placeId: extra.placeId || null,
    gameVersion: extra.gameVersion || null,
    blockedReason: extra.blockedReason || null,
    conflictNames: extra.conflictNames || null,
    evidenceSummary: extra.evidenceSummary || {},
    contributorHashes: [],
    rarityConfidence: extra.rarityConfidence || null,
    rarityEvidenceCount: extra.rarityEvidenceCount || 0,
    raritySources: extra.raritySources || [],
    rarityConflictValues: extra.rarityConflictValues || null,
    confirmationReason: extra.confirmationReason || null,
  };
}

function _evaluateConfirmation(entry, nameValidation, evidence) {
  if (entry.confidence === 'blocked' || entry.blockedReason) {
    entry.publicEligible = false;
    return { decision: 'blocked', reason: entry.blockedReason };
  }
  if (entry.confidence === 'conflict' || (entry.conflictNames && entry.conflictNames.length > 1)) {
    entry.confidence = 'conflict';
    entry.publicEligible = false;
    return { decision: 'conflict', reason: 'name_conflict', conflictNames: entry.conflictNames };
  }

  const verifiedSource = VERIFIED_CATCH_SOURCES.has(evidence.source);
  const nameKnown = !!(nameValidation && nameValidation.nameKnown);
  const hasImage = !!(nameValidation && (nameValidation.imageAssetId || nameValidation.imageUrl));

  if (entry.evidenceCount >= 2 || entry.uniqueUserCount >= 2) {
    if (!nameKnown) {
      entry.confidence = 'pending';
      entry.publicEligible = false;
      return { decision: 'pending', reason: 'name_not_validated' };
    }
    entry.confidence = 'confirmed';
    entry.publicEligible = true;
    entry.lastConfirmedAt = new Date().toISOString();
    entry.confirmationReason = entry.uniqueUserCount >= 2
      ? 'multi_user_confirmed' : 'repeated_observation_confirmed';
    return { decision: 'confirmed', reason: entry.confirmationReason };
  }

  if (entry.evidenceCount === 1 && verifiedSource && nameKnown && hasImage
      && evidence.cleanSingleDelta && !entry.conflictNames) {
    entry.confidence = 'confirmed';
    entry.publicEligible = true;
    entry.lastConfirmedAt = new Date().toISOString();
    entry.confirmationReason = 'high_confidence_single_clean_delta';
    return { decision: 'confirmed', reason: entry.confirmationReason };
  }

  entry.confidence = 'pending';
  entry.publicEligible = false;
  const reason = nameKnown ? 'awaiting_second_observation' : 'name_not_validated';
  return { decision: 'pending', reason };
}

function _applyRarity(entry, rarityCandidate, source) {
  if (!rarityCandidate || !rarityLabels.isRarityLabel(rarityCandidate)) return;
  const norm = normalizeRarity(rarityCandidate);
  if (!norm) return;
  if (entry.rarity && entry.rarity !== norm && entry.rarityConfidence === 'confirmed') {
    const conflicts = new Set(entry.rarityConflictValues || []);
    conflicts.add(entry.rarity);
    conflicts.add(norm);
    entry.rarityConflictValues = [...conflicts];
    entry.rarityConfidence = 'conflict';
    return;
  }
  if (!entry.rarity) {
    entry.rarity = norm;
    entry.normalizedRarity = norm;
    entry.rarityConfidence = 'pending';
    entry.rarityEvidenceCount = 1;
    entry.raritySources = [source || 'catch_delta'];
  } else if (entry.rarity === norm) {
    entry.rarityEvidenceCount = (entry.rarityEvidenceCount || 0) + 1;
    if (entry.rarityEvidenceCount >= 2) entry.rarityConfidence = 'confirmed';
  }
}

function blockEntry(itemId, learnedName, reason, extra) {
  _load();
  const id = sanitiseItemId(itemId);
  if (!id) return { ok: false, reason: 'invalid_id' };
  const entry = _makeEntry(id, learnedName || 'blocked', {
    confidence: 'blocked',
    blockedReason: reason,
    source: 'catch_delta',
    ...(extra || {}),
  });
  entry.publicEligible = false;
  _store.byItemId[id] = entry;
  _store.updatedAt = new Date().toISOString();
  _pushRecentEvent({ itemId: id, decision: 'blocked', reason, fishName: learnedName });
  _maybePersist();
  return { ok: true, reason, entry };
}

function _purgeForceBlocked() {
  for (const row of FORCE_BLOCKED) {
    if (!_store.byItemId[row.itemId]) {
      blockEntry(row.itemId, row.learnedName, row.reason, { forceBlocked: true });
    }
  }
}

/**
 * Submit compact catch-delta evidence to the global catalog.
 */
function submitEvidence(raw) {
  _load();
  const itemId = sanitiseItemId(raw && raw.itemId);
  const fishName = typeof raw.fishNameCandidate === 'string' ? raw.fishNameCandidate.trim() : '';
  const rarityCandidate = raw.rarityCandidate || null;
  const source = typeof raw.source === 'string' ? raw.source.slice(0, 40) : 'catch_notification';
  const now = new Date().toISOString();
  const userHash = raw.userIdHash || hashContributorId(raw.userId);

  const reject = (reason, extra) => {
    const result = { accepted: false, rejected: true, reason, ...(extra || {}) };
    _pushRecentEvent({
      itemId, decision: 'rejected', reason,
      fishName: fishName || rarityCandidate, rarity: rarityCandidate,
    });
    _lastIngestResult = result;
    _maybePersist();
    return result;
  };

  if (!itemId) return reject('invalid_id');
  if (learnedFishCatalog.isKnownNonFishId(itemId)) return reject('known_non_fish', { itemId });

  if (!fishName && rarityCandidate && rarityLabels.isRarityLabel(rarityCandidate)) {
    blockEntry(itemId, rarityCandidate, 'name_is_rarity_label', { sourceText: raw.sourceText });
    return reject('name_is_rarity_label', { itemId, rarityCandidate });
  }
  if (!fishName) return reject('no_valid_fish_name_candidate', { itemId });
  if (rarityLabels.isBlockedLearnName(fishName)) {
    blockEntry(itemId, fishName, rarityLabels.isRarityLabel(fishName)
      ? 'name_is_rarity_label' : 'name_is_status_label', { sourceText: raw.sourceText });
    return reject('name_is_rarity_label', { itemId, fishName });
  }

  const nameValidation = nameOnlyCatalog.validateFishName(fishName);
  if (!nameValidation.nameKnown && !VERIFIED_CATCH_SOURCES.has(source)) {
    return reject('name_not_validated_weak_source', { itemId, fishName, nameValidation });
  }

  let entry = _store.byItemId[itemId];
  if (entry && entry.confidence === 'blocked') {
    return reject(entry.blockedReason || 'blocked_history', { itemId });
  }

  if (entry && entry.fishName && normalizeFishName(entry.fishName) !== normalizeFishName(fishName)) {
    const conflicts = new Set([entry.fishName, fishName, ...(entry.conflictNames || [])]);
    entry.conflictNames = [...conflicts];
    entry.confidence = 'conflict';
    entry.publicEligible = false;
    entry.lastSeenAt = now;
    _store.updatedAt = now;
    _pushRecentEvent({ itemId, decision: 'conflict', reason: 'name_conflict', fishName });
    _lastIngestResult = { accepted: true, rejected: false, decision: 'conflict', itemId, entry };
    _maybePersist();
    _resetMergedFishCatalog();
    return _lastIngestResult;
  }

  if (!entry) {
    entry = _makeEntry(itemId, fishName, {
      source: 'catch_delta',
      gameId: raw.gameId || null,
      placeId: raw.placeId || null,
      gameVersion: raw.gameVersion || null,
    });
    _store.byItemId[itemId] = entry;
  }

  entry.evidenceCount = (entry.evidenceCount || 0) + 1;
  entry.lastSeenAt = now;
  if (userHash && !entry.contributorHashes.includes(userHash)) {
    entry.contributorHashes.push(userHash);
    entry.uniqueUserCount = entry.contributorHashes.length;
  }
  if (!entry.sources.includes('catch_delta')) entry.sources.push('catch_delta');
  entry.evidenceSummary = {
    lastSource: source,
    lastValidation: nameValidation.reason || null,
    lastDelta: raw.deltaAmount || 1,
    lastAt: now,
  };
  if (nameValidation.imageAssetId) entry.imageAssetId = nameValidation.imageAssetId;
  if (nameValidation.imageUrl) entry.imageUrl = nameValidation.imageUrl;

  _applyRarity(entry, rarityCandidate, source);

  const evalResult = _evaluateConfirmation(entry, nameValidation, {
    source,
    cleanSingleDelta: raw.cleanSingleDelta !== false,
  });
  entry.publicEligible = publicEligible(entry);
  if (entry.publicEligible) entry.lastConfirmedAt = now;

  _store.updatedAt = now;
  _pushRecentEvent({
    itemId,
    decision: evalResult.decision,
    reason: evalResult.reason,
    fishName,
    rarity: entry.rarity,
  });

  const result = {
    accepted: true,
    rejected: false,
    decision: evalResult.decision,
    reason: evalResult.reason,
    itemId,
    fishName,
    rarity: entry.rarity,
    rarityCandidate,
    nameValidation,
    entry: { ...entry, contributorHashes: undefined },
    publicEligible: entry.publicEligible,
    evidenceCount: entry.evidenceCount,
    uniqueUserCount: entry.uniqueUserCount,
  };
  _lastIngestResult = result;
  _maybePersist();
  if (entry.publicEligible) _resetMergedFishCatalog();
  return result;
}

function lookupById(itemId) {
  _load();
  const id = sanitiseItemId(itemId);
  if (!id) return null;
  const e = _store.byItemId[id];
  if (!e) return null;
  return { ...e, publicEligible: publicEligible(e), contributorHashes: undefined };
}

function getConfirmedMappings() {
  return getAllMappings().filter((e) => publicEligible(e));
}

function getAllMappings() {
  _load();
  return Object.values(_store.byItemId).map((e) => ({
    ...e,
    publicEligible: publicEligible(e),
    contributorHashes: undefined,
  }));
}

function getStats() {
  _load();
  const all = getAllMappings();
  return {
    enabled: true,
    storage: _store.storage || 'json',
    totalMappings: all.length,
    confirmedCount: all.filter((e) => e.confidence === 'confirmed' && e.publicEligible).length,
    pendingCount: all.filter((e) => e.confidence === 'pending').length,
    blockedCount: all.filter((e) => e.confidence === 'blocked').length,
    conflictCount: all.filter((e) => e.confidence === 'conflict').length,
    withImages: all.filter((e) => !!e.imageAssetId || !!e.imageUrl).length,
    withRarity: all.filter((e) => !!e.rarity).length,
    lastUpdatedAt: _store.updatedAt,
  };
}

function catalogMapForItemIds(itemIds) {
  const out = {};
  for (const rawId of itemIds || []) {
    const meta = lookupById(rawId);
    if (!meta) continue;
    out[String(rawId)] = {
      itemId: meta.itemId,
      fishName: meta.fishName,
      rarity: meta.rarity || null,
      confidence: meta.confidence,
      publicEligible: meta.publicEligible,
      evidenceCount: meta.evidenceCount,
      uniqueUserCount: meta.uniqueUserCount,
      imageUrlPresent: !!(meta.imageUrl || meta.imageAssetId),
      imageAssetId: meta.imageAssetId || null,
      source: meta.source,
      blockedReason: meta.blockedReason || null,
      conflictNames: meta.conflictNames || null,
    };
  }
  return out;
}

function getRecentEvents(limit = 10) {
  _load();
  return (_store.recentEvents || []).slice(-limit);
}

function getLastIngestResult() {
  return _lastIngestResult;
}

function getGlobalEvidenceStats() {
  const recent = getRecentEvents(20);
  return {
    globalEvidenceAccepted: recent.filter((e) => e.decision === 'confirmed' || e.decision === 'pending').length,
    globalEvidenceRejected: recent.filter((e) => e.decision === 'rejected' || e.decision === 'blocked').length,
    recentEvents: recent,
  };
}

function buildLiveCatchBinding(sessionDiscovery) {
  const nv = sessionDiscovery?.lastFishNameCandidate
    ? nameOnlyCatalog.validateFishName(sessionDiscovery.lastFishNameCandidate) : null;
  const globalResult = sessionDiscovery?.globalEvidence || getLastIngestResult();
  let lastDecision = sessionDiscovery?.promotionDecision || null;
  let lastRejectReason = null;
  if (sessionDiscovery?.rejectedEvents?.length) {
    lastRejectReason = sessionDiscovery.rejectedEvents[sessionDiscovery.rejectedEvents.length - 1].reason;
  }
  if (globalResult) {
    lastDecision = globalResult.decision || lastDecision;
    if (globalResult.rejected) lastRejectReason = globalResult.reason;
  }
  return {
    lastCatchTextRaw: sessionDiscovery?.lastParserRawText || null,
    lastFishNameCandidate: sessionDiscovery?.lastFishNameCandidate ?? null,
    lastRarityCandidate: sessionDiscovery?.lastRarityCandidate ?? null,
    lastCandidateValid: !!(nv && nv.nameKnown),
    lastCandidateValidationSource: nv && nv.reason ? nv.reason : null,
    lastDeltaCandidates: sessionDiscovery?.deltaCandidates
      || (sessionDiscovery?.lastInventoryDelta && sessionDiscovery.lastInventoryDelta.increased) || [],
    lastDecision,
    lastRejectReason,
    recentEvents: getRecentEvents(10),
    globalEvidence: globalResult || null,
  };
}

function seedAdminEntry(raw) {
  _load();
  const itemId = sanitiseItemId(raw.itemId);
  if (!itemId || !raw.fishName) return { ok: false };
  const now = new Date().toISOString();
  const entry = _makeEntry(itemId, raw.fishName, {
    confidence: 'confirmed',
    source: raw.source || 'admin_seed',
    sources: [raw.source || 'admin_seed'],
    rarity: raw.rarity || null,
  });
  entry.publicEligible = true;
  entry.evidenceCount = Math.max(1, Number(raw.evidenceCount) || 1);
  entry.uniqueUserCount = Math.max(1, Number(raw.uniqueUserCount) || 1);
  entry.lastConfirmedAt = now;
  entry.confirmationReason = 'admin_seed';
  if (raw.rarity) {
    entry.rarityConfidence = 'confirmed';
    entry.rarityEvidenceCount = 1;
  }
  _store.byItemId[itemId] = entry;
  _store.updatedAt = now;
  _maybePersist();
  _resetMergedFishCatalog();
  return { ok: true, entry };
}

function reloadFromDisk() {
  _store = null;
  return _load();
}

function _reset() {
  _store = null;
  _lastIngestResult = null;
  try {
    const sp = storePath();
    if (fs.existsSync(sp)) fs.unlinkSync(sp);
  } catch (_) { /* ignore */ }
}

module.exports = {
  storePath,
  get STORE_PATH() { return storePath(); },
  FORCE_BLOCKED,
  hashContributorId,
  normalizeFishName,
  publicEligible,
  submitEvidence,
  blockEntry,
  seedAdminEntry,
  lookupById,
  getAllMappings,
  getConfirmedMappings,
  getStats,
  catalogMapForItemIds,
  getRecentEvents,
  getLastIngestResult,
  getGlobalEvidenceStats,
  buildLiveCatchBinding,
  reloadFromDisk,
  _reset,
};
