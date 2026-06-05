'use strict';
/**
 * Persistent learned fish catalog (BLOCKER10M/10P).
 * itemId -> fish name from catch-delta — never rarity labels.
 */

const path = require('path');
const fs = require('fs');
const rarityLabels = require('./fishitRarityLabels');
const catchNameParser = require('./fishitCatchNameParser');

function storePath() {
  return process.env.FISHIT_LEARNED_FISH_CATALOG_PATH
    || path.join(__dirname, '..', 'data', 'fishit_learned_fish_catalog.json');
}

const HIGH_CONFIDENCE_SOURCES = new Set([
  'catch_delta_high_confidence',
  'live_roblox_catch_delta',
  'manual_confirmed',
  'seed_confirmed',
]);

const PENDING_SOURCES = new Set([
  'catch_delta_pending',
  'catch_delta_low_confidence',
]);

const KNOWN_NON_FISH_IDS = new Set(['10', '388', '990']);

/** Poisoned mappings quarantined on load (BLOCKER10P). */
const FORCE_BLOCKED = [
  { itemId: '196', learnedName: 'Forgotten', reason: 'name_is_rarity_label' },
];

let _store = null;

function _defaultStore() {
  return { updatedAt: null, byItemId: {}, blockedByItemId: {} };
}

function _load() {
  if (_store) return _store;
  try {
    const sp = storePath();
    if (fs.existsSync(sp)) {
      const raw = JSON.parse(fs.readFileSync(sp, 'utf8'));
      _store = {
        updatedAt: raw.updatedAt || null,
        byItemId: (raw.byItemId && typeof raw.byItemId === 'object') ? raw.byItemId : {},
        blockedByItemId: (raw.blockedByItemId && typeof raw.blockedByItemId === 'object')
          ? raw.blockedByItemId : {},
      };
      purgePoisonedMappings();
      return _store;
    }
  } catch (_) { /* fall through */ }
  _store = _defaultStore();
  purgePoisonedMappings();
  return _store;
}

function _persist() {
  const sp = storePath();
  const dir = path.dirname(sp);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(sp, JSON.stringify(_store, null, 2), 'utf8');
}

function sanitiseItemId(raw) {
  const id = String(raw || '').trim();
  return /^\d+$/.test(id) ? id : null;
}

function isHighConfidence(entry) {
  if (!entry) return false;
  if (entry.confidence === 1 || entry.confidence === 1.0) return true;
  return HIGH_CONFIDENCE_SOURCES.has(entry.source);
}

function publicEligible(entry) {
  if (!entry || entry.category !== 'fish' || !entry.name) return false;
  if (/^Item #\d+$/i.test(entry.name)) return false;
  if (rarityLabels.isBlockedLearnName(entry.name)) return false;
  if (entry.source === 'live_roblox_catch_delta'
      && entry.proof?.evidenceSourceMode === 'live_roblox'
      && entry.proof?.promotionDecision === 'confirmed') {
    return true;
  }
  if (!isHighConfidence(entry)) return false;
  const obs = entry.proof && entry.proof.observationCount;
  const nameValidated = entry.proof && entry.proof.nameValidated === true;
  if (nameValidated && obs >= 1) return true;
  if (obs >= 2) return true;
  return false;
}

function isKnownNonFishId(itemId) {
  return KNOWN_NON_FISH_IDS.has(String(itemId));
}

function blockEntry(itemId, name, reason, extra) {
  _load();
  const id = sanitiseItemId(itemId);
  if (!id) return false;
  if (_store.byItemId[id]) delete _store.byItemId[id];
  _store.blockedByItemId[id] = {
    itemId: id,
    learnedName: name || null,
    reason: reason || 'blocked',
    blockedAt: new Date().toISOString(),
    ...(extra || {}),
  };
  _store.updatedAt = new Date().toISOString();
  _maybePersist();
  return true;
}

function _maybePersist() {
  if (process.env.NODE_ENV !== 'test' || process.env.FISHIT_LEARNED_PERSIST === '1') _persist();
}

function removeEntry(itemId) {
  _load();
  const id = sanitiseItemId(itemId);
  if (!id || !_store.byItemId[id]) return false;
  delete _store.byItemId[id];
  _store.updatedAt = new Date().toISOString();
  _maybePersist();
  return true;
}

function purgePoisonedMappings() {
  if (!_store) return { removed: 0, blocked: 0 };
  let removed = 0;
  let blocked = 0;
  for (const [id, entry] of Object.entries(_store.byItemId || {})) {
    if (!entry || rarityLabels.isBlockedLearnName(entry.name)) {
      blockEntry(id, entry && entry.name, 'name_is_rarity_label', {
        previousSource: entry && entry.source,
        purged: true,
      });
      removed += 1;
      blocked += 1;
    }
  }
  for (const row of FORCE_BLOCKED) {
    if (_store.byItemId[row.itemId]) {
      blockEntry(row.itemId, row.learnedName, row.reason, { forceBlocked: true });
      removed += 1;
      blocked += 1;
    } else if (!_store.blockedByItemId[row.itemId]) {
      blockEntry(row.itemId, row.learnedName, row.reason, { forceBlocked: true });
      blocked += 1;
    }
  }
  return { removed, blocked };
}

function ingestEntry(raw, mainCatalogLookup, nameValidation) {
  const itemId = sanitiseItemId(raw && raw.itemId);
  if (!itemId) return { updated: false, reason: 'invalid_id' };
  if (isKnownNonFishId(itemId)) return { updated: false, reason: 'known_non_fish', itemId };

  const name = typeof raw.name === 'string' ? raw.name.trim().slice(0, 100) : '';
  if (!name) return { updated: false, reason: 'empty_name' };

  if (rarityLabels.isBlockedLearnName(name)) {
    blockEntry(itemId, name, rarityLabels.isRarityLabel(name) ? 'name_is_rarity_label' : 'name_is_status_label', {
      rejectedLearnedName: name,
      sourceText: raw.proof && raw.proof.catchName,
    });
    return {
      updated: false,
      reason: rarityLabels.isRarityLabel(name) ? 'name_is_rarity_label' : 'name_is_status_label',
      itemId,
      rejectedLearnedName: name,
    };
  }

  _load();
  if (_store.blockedByItemId[itemId]) {
    const prev = _store.blockedByItemId[itemId];
    if (prev.learnedName === name || prev.reason === 'name_is_rarity_label') {
      return {
        updated: false,
        reason: prev.reason || 'blocked_history',
        itemId,
        rejectedLearnedName: name,
      };
    }
  }

  const category = typeof raw.category === 'string' ? raw.category.trim().toLowerCase() : 'fish';
  const source = typeof raw.source === 'string' ? raw.source.slice(0, 80) : 'unknown';
  const confidence = Number.isFinite(Number(raw.confidence)) ? Number(raw.confidence) : null;
  const proof = (raw.proof && typeof raw.proof === 'object') ? raw.proof : null;
  const now = new Date().toISOString();
  const nameValidated = !!(nameValidation && nameValidation.nameKnown);
  const validationReason = nameValidation && nameValidation.reason;

  const existing = _store.byItemId[itemId];

  if (mainCatalogLookup) {
    const main = mainCatalogLookup(itemId);
    if (main && main.name && main.name !== name) {
      const mainHigh = main.source === 'seed_confirmed' || main.source === 'manual_confirmed'
        || main.confidence === 'confirmed';
      if (mainHigh) return { updated: false, reason: 'main_catalog_differs', itemId, existingName: main.name };
      if (main.category && main.category !== 'fish' && category === 'fish') {
        return { updated: false, reason: 'main_non_fish_protected', itemId, existingName: main.name };
      }
    }
  }

  const existingBase = existing
    ? (catchNameParser.baseFishNameForConflict(existing.name) || existing.name)
    : null;
  const incomingBase = catchNameParser.baseFishNameForConflict(name) || name;
  if (existing && existingBase && incomingBase && existingBase !== incomingBase) {
    blockEntry(itemId, existingBase, 'name_conflict_quarantine', { conflictName: incomingBase });
    return { updated: false, reason: 'live_catch_conflict_base_name', itemId, conflictNames: [existingBase, incomingBase] };
  }

  if (existing && publicEligible(existing) && existingBase === incomingBase) {
    return {
      updated: false,
      reason: 'already_confirmed',
      itemId,
      entry: existing,
      publicEligible: true,
    };
  }

  const observationCount = (existing && existing.proof && existing.proof.observationCount) || 0;
  const nextObs = (existing && existingBase === incomingBase) ? observationCount + 1 : 1;

  let finalSource = source;
  let finalConfidence = confidence != null ? confidence : 0.5;
  let promotionDecision = 'pending';
  let promotionReason = 'first_observation_pending';

  const isLiveRobloxCatch = source === 'live_roblox_catch_delta'
    && proof?.evidenceSourceMode === 'live_roblox';
  const canPromoteImmediate = nameValidated
    && nextObs >= 1
    && HIGH_CONFIDENCE_SOURCES.has('catch_delta_high_confidence')
    && source === 'catch_delta_high_confidence';

  if (isLiveRobloxCatch && nextObs >= 1) {
    finalSource = 'live_roblox_catch_delta';
    finalConfidence = 0.85;
    promotionDecision = 'confirmed';
    promotionReason = 'live_roblox_single_delta_public';
  } else if (nameValidated && nextObs >= 2) {
    finalSource = 'catch_delta_high_confidence';
    finalConfidence = 1;
    promotionDecision = 'confirmed';
    promotionReason = 'repeated_observation_name_validated';
  } else if (canPromoteImmediate && nameValidated) {
    finalSource = 'catch_delta_high_confidence';
    finalConfidence = 1;
    promotionDecision = 'confirmed';
    promotionReason = 'verified_name_single_delta';
  } else if (nextObs >= 2 && existing && existing.name === name) {
    finalSource = 'catch_delta_high_confidence';
    finalConfidence = 1;
    promotionDecision = 'confirmed';
    promotionReason = 'repeated_observation';
  } else {
    finalSource = 'catch_delta_pending';
    finalConfidence = 0.5;
    promotionDecision = 'pending';
    promotionReason = nameValidated ? 'awaiting_second_observation' : 'name_not_validated';
  }

  const mergedProof = {
    ...(existing && existing.proof ? existing.proof : {}),
    ...(proof || {}),
    observationCount: nextObs,
    lastObservedAt: now,
    nameValidated,
    validationReason: validationReason || null,
    promotionDecision,
    promotionReason,
    evidenceSources: nameValidation && nameValidation.reason ? [nameValidation.reason] : [],
  };

  const entry = {
    itemId,
    name: incomingBase,
    displayName: typeof raw.displayName === 'string' ? raw.displayName.trim() : name,
    mutation: raw.mutation || null,
    weightKg: raw.weightKg != null ? raw.weightKg : null,
    category: category === 'fish' ? 'fish' : category,
    source: finalSource,
    confidence: finalConfidence,
    proof: mergedProof,
    updatedAt: now,
    publicEligible: false,
  };
  entry.publicEligible = publicEligible(entry);

  const wasNew = !existing;
  _store.byItemId[itemId] = entry;
  _store.updatedAt = now;
  _maybePersist();

  return {
    updated: true,
    reason: wasNew ? 'inserted' : 'updated',
    itemId,
    entry,
    publicEligible: entry.publicEligible,
    promotionDecision,
    promotionReason,
    observationCount: nextObs,
  };
}

function ingestBatch(entries, mainCatalogLookup, validateName) {
  if (!Array.isArray(entries) || entries.length === 0) return [];
  const results = [];
  for (const raw of entries.slice(0, 30)) {
    const nv = validateName && raw.name ? validateName(raw.name) : null;
    results.push(ingestEntry(raw, mainCatalogLookup, nv));
  }
  return results;
}

function lookupById(itemId) {
  _load();
  const id = sanitiseItemId(itemId);
  if (!id) return null;
  const e = _store.byItemId[id];
  if (!e) return null;
  return { ...e, publicEligible: publicEligible(e) };
}

function getAllMappings() {
  _load();
  return Object.values(_store.byItemId).map((e) => ({
    ...e,
    publicEligible: publicEligible(e),
  }));
}

function getBlockedMappings() {
  _load();
  return Object.values(_store.blockedByItemId || {});
}

function getHighConfidenceFishIds() {
  return getAllMappings()
    .filter((e) => e.publicEligible)
    .map((e) => e.itemId);
}

function reloadFromDisk() {
  _store = null;
  return _load();
}

function _reset() {
  _store = _defaultStore();
  try {
    const sp = storePath();
    if (fs.existsSync(sp)) fs.unlinkSync(sp);
  } catch (_) { /* ignore */ }
}

module.exports = {
  storePath,
  get STORE_PATH() { return storePath(); },
  HIGH_CONFIDENCE_SOURCES,
  PENDING_SOURCES,
  KNOWN_NON_FISH_IDS,
  FORCE_BLOCKED,
  isKnownNonFishId,
  blockEntry,
  removeEntry,
  purgePoisonedMappings,
  ingestEntry,
  ingestBatch,
  lookupById,
  getAllMappings,
  getBlockedMappings,
  getHighConfidenceFishIds,
  isHighConfidence,
  publicEligible,
  reloadFromDisk,
  _reset,
};
