'use strict';
/**
 * BLOCKER10Z11 — safe global learning pipeline after catalog reset.
 *
 * Records evidence without promoting ambiguous itemId-only or poisoned rows.
 */

const globalDb = require('./fishitGlobalDb');
const catchNameParser = require('./fishitCatchNameParser');
const protectedFishNames = require('./fishitProtectedFishNames');
const dengBotCatalog = require('./fishitDengFishItBotCatalog');

const PHANTOM_ITEM_IDS = new Set(['1008']);
const AMBIGUOUS_CONTAINER_IDS = new Set(['267']);

const STATUS = {
  PENDING: 'pending',
  CONFIRMED: 'confirmed',
  QUARANTINED: 'quarantined',
  BLOCKED: 'blocked',
};

/** In-process learning ledger (debug + promotion audit). */
const _records = new Map();

function _normItemId(itemId) {
  return itemId != null ? String(itemId).trim() : null;
}

function _hasSnapshotMetadata(raw) {
  const metaName = raw?.metadataFishName || raw?.metadata_fish_name;
  const metaId = raw?.metadataFishId || raw?.metadata_fish_id;
  return !!(String(metaName || '').trim() || String(metaId || '').trim());
}

function _isTrustedCatchName(raw) {
  if (raw?.nameValidated === true) return true;
  if (raw?.proof?.nameValidated === true) return true;
  if (raw?.sourcePayloadType === 'inventory_snapshot' && raw?.metadataFishName) return true;
  if (raw?.source === 'live_roblox_catch_delta' && raw?.proof?.nameValidated === true) return true;
  return false;
}

function _resolveBaseName(raw) {
  let base = raw?.baseFishName || raw?.parsed_base_name;
  if (!base) {
    const parsed = catchNameParser.parseCatchInput({
      fishName: raw?.rawName || raw?.name || raw?.raw_name,
      rawText: raw?.rawName || raw?.name,
    });
    base = parsed.baseFishName || parsed.fishNameCandidate;
  }
  if (base && protectedFishNames.isProtectedBaseName(base)) {
    base = protectedFishNames.normalizeProtected(base);
  }
  return base ? String(base).trim() : null;
}

function _recordKey(itemId, baseName) {
  if (itemId && baseName) return `id:${itemId}|${globalDb.normalizeNamePunct(baseName)}`;
  if (baseName) return `name:${globalDb.normalizeNamePunct(baseName)}`;
  if (itemId) return `id:${itemId}`;
  return null;
}

function evaluateEvidence(raw) {
  const itemId = _normItemId(raw?.itemId || raw?.item_id);
  const baseName = _resolveBaseName(raw);
  const hasMeta = _hasSnapshotMetadata(raw);
  const trustedName = _isTrustedCatchName(raw) || !!raw?.metadataFishName;
  const contributorHash = globalDb.hashContributor(raw?.userId || raw?.userIdHash || raw?.sessionKey);

  if (itemId && PHANTOM_ITEM_IDS.has(itemId) && !hasMeta) {
    return {
      status: STATUS.BLOCKED,
      reason: 'phantom_item_id_without_metadata',
      itemId,
      baseName,
      evidenceType: 'item_id_only',
    };
  }

  if (itemId && AMBIGUOUS_CONTAINER_IDS.has(itemId) && !hasMeta && !trustedName) {
    return {
      status: STATUS.QUARANTINED,
      reason: 'ambiguous_container_without_metadata',
      itemId,
      baseName,
      evidenceType: 'ambiguous_container',
    };
  }

  if (!baseName && !hasMeta) {
    return {
      status: STATUS.BLOCKED,
      reason: 'item_id_only_no_name',
      itemId,
      evidenceType: 'item_id_only',
    };
  }

  if (/^unknown fish #\d+/i.test(String(baseName || raw?.rawName || raw?.name || ''))) {
    return {
      status: STATUS.BLOCKED,
      reason: 'unknown_fish_placeholder',
      itemId,
      baseName,
      evidenceType: 'guessed_name',
    };
  }

  const botHit = baseName ? dengBotCatalog.lookupRarity(baseName) : null;
  const evidenceType = hasMeta
    ? 'snapshot_metadata'
    : (trustedName ? 'trusted_catch_name' : 'weak_observation');

  let status = STATUS.PENDING;
  let confidence = 'live_observed';

  if (botHit?.rarity) {
    status = STATUS.CONFIRMED;
    confidence = 'deng_fish_it_bot';
  } else if (hasMeta && trustedName) {
    status = STATUS.PENDING;
    confidence = 'metadata_backed';
  } else if (trustedName) {
    status = STATUS.PENDING;
    confidence = 'single_user_catch';
  } else {
    status = STATUS.PENDING;
    confidence = 'weak_observation';
  }

  return {
    status,
    reason: null,
    itemId,
    baseName,
    fishName: baseName,
    rarity: botHit?.rarity || raw?.rarity || null,
    raritySource: botHit?.raritySource || null,
    evidenceType,
    evidenceSource: raw?.sourcePayloadType || raw?.source || 'inventory_snapshot',
    contributorHash,
    gameId: raw?.gameId || raw?.game_id || null,
    placeId: raw?.placeId || raw?.place_id || null,
    confidence,
    hasSnapshotMetadata: hasMeta,
    trustedCatchName: trustedName,
  };
}

function recordLearningEvidence(raw) {
  const ev = evaluateEvidence(raw);
  const key = _recordKey(ev.itemId, ev.baseName);
  if (!key) return { ...ev, accepted: false, reason: 'no_key' };

  const now = new Date().toISOString();
  const existing = _records.get(key) || {
    fishName: ev.baseName,
    baseFishName: ev.baseName,
    itemId: ev.itemId,
    rarity: ev.rarity,
    raritySource: ev.raritySource,
    evidenceType: ev.evidenceType,
    evidenceSource: ev.evidenceSource,
    contributorHashes: new Set(),
    firstSeenAt: now,
    lastSeenAt: now,
    observationCount: 0,
    uniqueUserCount: 0,
    status: ev.status,
    conflicts: [],
  };

  existing.observationCount += 1;
  existing.lastSeenAt = now;
  if (ev.contributorHash) existing.contributorHashes.add(ev.contributorHash);
  existing.uniqueUserCount = existing.contributorHashes.size;

  if (ev.status === STATUS.BLOCKED || ev.status === STATUS.QUARANTINED) {
    existing.status = ev.status;
    existing.blockReason = ev.reason;
    _records.set(key, existing);
    return {
      ...ev,
      accepted: ev.status !== STATUS.BLOCKED,
      decision: ev.status,
      record: _serializeRecord(existing),
    };
  }

  if (ev.rarity && existing.rarity && ev.rarity !== existing.rarity) {
    existing.conflicts.push({ incoming: ev.rarity, existing: existing.rarity, at: now });
    existing.status = STATUS.QUARANTINED;
    _records.set(key, existing);
    return { ...ev, accepted: true, decision: 'quarantined', conflict: true, record: _serializeRecord(existing) };
  }

  if (ev.rarity) {
    existing.rarity = ev.rarity;
    existing.raritySource = ev.raritySource;
  }

  const botConfirmed = ev.raritySource === dengBotCatalog.SOURCE_ID;
  const multiUser = existing.uniqueUserCount >= 2 || existing.observationCount >= 3;
  const metaStrong = ev.hasSnapshotMetadata && ev.trustedCatchName;

  if (botConfirmed) {
    existing.status = STATUS.CONFIRMED;
    existing.confidence = 'deng_fish_it_bot';
  } else if (metaStrong && multiUser) {
    existing.status = STATUS.CONFIRMED;
    existing.confidence = 'multi_user_metadata';
  } else if (metaStrong) {
    existing.status = STATUS.PENDING;
    existing.confidence = 'metadata_pending';
  } else {
    existing.status = STATUS.PENDING;
    existing.confidence = existing.confidence || 'live_observed';
  }

  _records.set(key, existing);
  return {
    ...ev,
    accepted: true,
    decision: existing.status,
    promoted: existing.status === STATUS.CONFIRMED,
    record: _serializeRecord(existing),
  };
}

function _serializeRecord(rec) {
  return {
    fishName: rec.fishName,
    baseFishName: rec.baseFishName,
    itemId: rec.itemId,
    rarity: rec.rarity,
    raritySource: rec.raritySource,
    evidenceType: rec.evidenceType,
    evidenceSource: rec.evidenceSource,
    contributorHash: rec.contributorHashes ? [...rec.contributorHashes].slice(-1)[0] : null,
    uniqueUserCount: rec.uniqueUserCount,
    observationCount: rec.observationCount,
    confidence: rec.confidence,
    status: rec.status,
    blockReason: rec.blockReason || null,
    conflicts: rec.conflicts || [],
    firstSeenAt: rec.firstSeenAt,
    lastSeenAt: rec.lastSeenAt,
  };
}

function buildGlobalLearningProof(limit = 25) {
  const records = [..._records.values()].map(_serializeRecord);
  const byStatus = {};
  for (const r of records) {
    byStatus[r.status] = (byStatus[r.status] || 0) + 1;
  }
  return {
    promotionRules: [
      'deng_fish_it_bot canonical entry confirms immediately',
      'snapshot metadata + trusted catch name → pending until multi-user',
      'ambiguous container id 267 without metadata → quarantined',
      'phantom itemId 1008 without metadata → blocked',
      'itemId-only without name/metadata → blocked',
    ],
    blockedItemIds: [...PHANTOM_ITEM_IDS, ...AMBIGUOUS_CONTAINER_IDS],
    statusCounts: byStatus,
    recentRecords: records.slice(-limit).reverse(),
    totalRecords: records.length,
  };
}

function _reset() {
  _records.clear();
}

module.exports = {
  STATUS,
  PHANTOM_ITEM_IDS,
  AMBIGUOUS_CONTAINER_IDS,
  evaluateEvidence,
  recordLearningEvidence,
  buildGlobalLearningProof,
  _reset,
};
