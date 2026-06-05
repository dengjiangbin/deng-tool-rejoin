'use strict';
/**
 * Catch notification + inventory delta name catalog discovery (BLOCKER10M/10O).
 */

const learnedFishCatalog = require('./fishitLearnedFishCatalog');

const NON_FISH_PHRASES = [
  'you caught',
  'new fish',
  'inventory full',
  'equipped',
  'sold',
  'purchased',
  'quest',
  'level up',
  'achievement',
  'warning',
  'error',
  'disconnect',
];

const NOTIFICATION_SUFFIX_RE = /\s*(?:!|\.|kg|lbs|lb)\s*$/i;
const CATCH_STALE_SECONDS = Number(process.env.FISHIT_CATCH_STALE_SECONDS || 180);
const HIGH_CONFIDENCE_CATCH_SOURCES = new Set(['catch_notification', 'catch_event']);

function normalizeCatchFishName(raw) {
  if (raw == null) return null;
  let s = String(raw).trim();
  if (!s) return null;
  s = s.replace(NOTIFICATION_SUFFIX_RE, '').trim();
  for (const phrase of NON_FISH_PHRASES) {
    if (s.toLowerCase().includes(phrase)) return null;
  }
  if (s.length < 2 || s.length > 80) return null;
  if (/^\d+$/.test(s)) return null;
  if (/^item\s*#\s*\d+$/i.test(s)) return null;
  if (/^[\d.,\s]+(?:kg|lb)?$/i.test(s)) return null;
  return s;
}

function sanitisePendingCatch(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const fishName = normalizeCatchFishName(raw.fishName ?? raw.name);
  if (!fishName) return null;
  const source = typeof raw.source === 'string' ? raw.source.slice(0, 40) : 'catch_notification';
  let detectedAt = raw.detectedAt;
  if (typeof detectedAt === 'number') {
    detectedAt = new Date(detectedAt * 1000).toISOString();
  } else if (typeof detectedAt !== 'string') {
    detectedAt = new Date().toISOString();
  }
  return { fishName, detectedAt, source };
}

function isCatchStale(pending) {
  if (!pending || !pending.detectedAt) return false;
  const t = Date.parse(pending.detectedAt);
  if (!Number.isFinite(t)) return false;
  return (Date.now() - t) > CATCH_STALE_SECONDS * 1000;
}

function buildItemCountsFromItems(items) {
  const counts = {};
  if (!Array.isArray(items)) return counts;
  for (const it of items) {
    if (!it || it.itemId == null) continue;
    const id = String(it.itemId).trim();
    if (!/^\d+$/.test(id)) continue;
    const amt = Number(it.amount);
    counts[id] = (counts[id] || 0) + (Number.isFinite(amt) && amt > 0 ? Math.floor(amt) : 1);
  }
  return counts;
}

function sanitiseCountMap(raw) {
  if (!raw || typeof raw !== 'object') return {};
  const out = {};
  for (const [k, v] of Object.entries(raw)) {
    const id = String(k).trim();
    if (!/^\d+$/.test(id)) continue;
    const n = Number(v);
    if (Number.isFinite(n) && n >= 0) out[id] = Math.floor(n);
  }
  return out;
}

function computeIncreasedIds(previousCounts, currentCounts) {
  const increased = [];
  const prev = previousCounts || {};
  const cur = currentCounts || {};
  for (const id of Object.keys(prev)) {
    const before = prev[id] || 0;
    const after = cur[id] || 0;
    if (after > before) {
      increased.push({
        itemId: id,
        beforeAmount: before,
        afterAmount: after,
        delta: after - before,
      });
    }
  }
  return increased;
}

function isKnownNonFishItemId(itemId, mainCatalogLookup) {
  if (learnedFishCatalog.isKnownNonFishId(itemId)) return true;
  const main = mainCatalogLookup ? mainCatalogLookup(itemId) : null;
  if (!main) return false;
  const cat = String(main.category || '').toLowerCase();
  return cat === 'rod' || cat === 'rods' || cat === 'bait' || cat === 'items';
}

function resolveLearnSource(pending, increasedCount) {
  const highCatch = HIGH_CONFIDENCE_CATCH_SOURCES.has(pending.source);
  if (increasedCount === 1 && highCatch) {
    return { source: 'catch_delta_high_confidence', confidence: 1.0, immediate: true };
  }
  if (increasedCount === 1) {
    return { source: 'catch_delta_pending', confidence: 0.5, immediate: false };
  }
  return { source: 'catch_delta_low_confidence', confidence: 0.3, immediate: false };
}

/**
 * Apply catch-delta learning from pending catch + count maps.
 */
function processCatchDelta({
  pendingCatch,
  previousItemCounts,
  currentItems,
  ingestLearned,
  mainCatalogLookup,
  uploadFailed,
}) {
  const discovery = {
    lastPendingCatchName: null,
    lastCatchAt: null,
    previousInventoryCounts: null,
    currentInventoryCounts: null,
    lastInventoryDelta: null,
    deltaCandidates: [],
    learnedMappings: [],
    pendingLowConfidenceMappings: [],
    rejectedEvents: [],
  };

  if (uploadFailed) {
    discovery.rejectedEvents.push({ reason: 'upload_failed' });
    return discovery;
  }

  const pending = sanitisePendingCatch(pendingCatch);
  if (!pending) {
    if (pendingCatch) {
      discovery.rejectedEvents.push({ reason: 'no_catch_name', raw: pendingCatch });
    } else {
      discovery.rejectedEvents.push({ reason: 'no_catch_name' });
    }
    return discovery;
  }
  discovery.lastPendingCatchName = pending;
  discovery.lastCatchAt = pending.detectedAt;

  if (isCatchStale(pending)) {
    discovery.rejectedEvents.push({ reason: 'stale_catch', catchName: pending.fishName, detectedAt: pending.detectedAt });
    return discovery;
  }

  const prev = sanitiseCountMap(previousItemCounts);
  const cur = buildItemCountsFromItems(currentItems);
  discovery.previousInventoryCounts = prev;
  discovery.currentInventoryCounts = cur;

  if (!prev || Object.keys(prev).length === 0) {
    discovery.rejectedEvents.push({ reason: 'no_previous_inventory', catchName: pending.fishName });
    return discovery;
  }

  const increased = computeIncreasedIds(prev, cur);
  discovery.lastInventoryDelta = { increased, previousCounts: prev, currentCounts: cur };
  discovery.deltaCandidates = increased;

  if (increased.length === 0) {
    discovery.rejectedEvents.push({ reason: 'no_delta', catchName: pending.fishName });
    return discovery;
  }

  if (increased.length > 1) {
    for (const inc of increased) {
      if (isKnownNonFishItemId(inc.itemId, mainCatalogLookup)) {
        discovery.rejectedEvents.push({
          reason: 'known_non_fish',
          itemId: inc.itemId,
          catchName: pending.fishName,
        });
        continue;
      }
      discovery.pendingLowConfidenceMappings.push({
        itemId: inc.itemId,
        candidateName: pending.fishName,
        source: 'catch_delta_low_confidence',
        confidence: 0.3,
        proof: {
          beforeAmount: inc.beforeAmount,
          afterAmount: inc.afterAmount,
          delta: inc.delta,
          catchName: pending.fishName,
          ambiguous: true,
        },
        publicEligible: false,
      });
    }
    discovery.rejectedEvents.push({
      reason: 'multiple_delta_candidates',
      catchName: pending.fishName,
      candidates: increased.map((i) => i.itemId),
    });
    return discovery;
  }

  const inc = increased[0];
  if (isKnownNonFishItemId(inc.itemId, mainCatalogLookup)) {
    discovery.rejectedEvents.push({
      reason: 'known_non_fish',
      itemId: inc.itemId,
      catchName: pending.fishName,
    });
    return discovery;
  }

  const main = mainCatalogLookup ? mainCatalogLookup(inc.itemId) : null;
  if (main && main.category && main.category !== 'fish'
      && (main.category === 'rod' || main.category === 'rods' || main.category === 'bait')) {
    discovery.rejectedEvents.push({
      reason: 'known_non_fish',
      itemId: inc.itemId,
      existingName: main.name,
      catchName: pending.fishName,
    });
    return discovery;
  }

  const learnMeta = resolveLearnSource(pending, 1);
  const mapping = {
    itemId: inc.itemId,
    name: pending.fishName,
    category: 'fish',
    source: learnMeta.source,
    confidence: learnMeta.confidence,
    proof: {
      beforeAmount: inc.beforeAmount,
      afterAmount: inc.afterAmount,
      delta: inc.delta,
      catchName: pending.fishName,
      catchSource: pending.source,
      catchAt: pending.detectedAt,
    },
  };
  const ingestResult = ingestLearned(mapping);
  const learnedItem = {
    itemId: inc.itemId,
    learnedName: pending.fishName,
    source: ingestResult.entry ? ingestResult.entry.source : learnMeta.source,
    beforeAmount: inc.beforeAmount,
    afterAmount: inc.afterAmount,
    publicEligible: !!(ingestResult.entry && ingestResult.entry.publicEligible),
    ingest: ingestResult,
  };

  if (ingestResult.reason === 'already_confirmed') {
    discovery.rejectedEvents.push({
      reason: 'already_confirmed',
      itemId: inc.itemId,
      catchName: pending.fishName,
    });
    return discovery;
  }

  if (ingestResult.updated !== false && learnedItem.publicEligible) {
    discovery.learnedMappings.push(learnedItem);
  } else if (ingestResult.updated !== false && !learnedItem.publicEligible) {
    discovery.pendingLowConfidenceMappings.push({
      itemId: inc.itemId,
      candidateName: pending.fishName,
      source: ingestResult.entry ? ingestResult.entry.source : 'catch_delta_pending',
      confidence: ingestResult.entry ? ingestResult.entry.confidence : 0.5,
      proof: mapping.proof,
      publicEligible: false,
    });
  } else {
    discovery.rejectedEvents.push({
      reason: ingestResult.reason || 'low_confidence',
      itemId: inc.itemId,
      catchName: pending.fishName,
    });
  }
  return discovery;
}

function buildNameCatalogDiscoveryForDebug(sessionDiscovery, learnedCatalog, sessionData) {
  const learned = learnedCatalog.getAllMappings();
  const unresolved = [];
  for (const m of learned) {
    if (!m.publicEligible) {
      unresolved.push({ itemId: m.itemId, name: m.name, reason: 'low_confidence_pending' });
    }
  }
  const pendingCatch = sessionDiscovery?.lastPendingCatchName || sessionData?.lastPendingCatchName || null;
  return {
    lastPendingCatchName: pendingCatch,
    lastCatchAt: sessionDiscovery?.lastCatchAt || (pendingCatch && pendingCatch.detectedAt) || null,
    previousInventoryCounts: sessionDiscovery?.previousInventoryCounts
      || sessionDiscovery?.lastInventoryDelta?.previousCounts || null,
    currentInventoryCounts: sessionDiscovery?.currentInventoryCounts
      || sessionDiscovery?.lastInventoryDelta?.currentCounts || null,
    lastInventoryDelta: sessionDiscovery?.lastInventoryDelta || null,
    deltaCandidates: sessionDiscovery?.deltaCandidates
      || (sessionDiscovery?.lastInventoryDelta && sessionDiscovery.lastInventoryDelta.increased) || [],
    learnedMappings: sessionDiscovery?.learnedMappings || [],
    pendingLowConfidenceMappings: sessionDiscovery?.pendingLowConfidenceMappings || [],
    rejectedEvents: sessionDiscovery?.rejectedEvents || [],
    persistentLearnedCount: learned.length,
    persistentHighConfidence: learned.filter((e) => e.publicEligible).map((e) => ({
      itemId: e.itemId,
      learnedName: e.name,
      source: e.source,
      publicEligible: true,
      observationCount: e.proof && e.proof.observationCount,
    })),
    unresolvedSample: unresolved.slice(0, 10),
  };
}

function unresolvedReasonForItem(itemId, learnedLookup, mainLookup) {
  const learned = learnedLookup(itemId);
  if (learned && learned.publicEligible) return null;
  const main = mainLookup(itemId);
  if (main && main.name && !/^Item #/i.test(main.name)) return null;
  return 'no_name_catalog_match_yet';
}

module.exports = {
  normalizeCatchFishName,
  sanitisePendingCatch,
  buildItemCountsFromItems,
  sanitiseCountMap,
  computeIncreasedIds,
  processCatchDelta,
  buildNameCatalogDiscoveryForDebug,
  unresolvedReasonForItem,
  CATCH_STALE_SECONDS,
  KNOWN_NON_FISH_IDS: learnedFishCatalog.KNOWN_NON_FISH_IDS,
};
