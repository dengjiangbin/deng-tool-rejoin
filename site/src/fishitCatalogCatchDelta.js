'use strict';
/**
 * Catch notification + inventory delta name catalog discovery (BLOCKER10M).
 */

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
  const detectedAt = typeof raw.detectedAt === 'number' ? raw.detectedAt
    : (typeof raw.detectedAt === 'string' ? raw.detectedAt : new Date().toISOString());
  return { fishName, detectedAt, source };
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
  // Only compare ids present in the previous snapshot (avoid 0 -> N false positives).
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

/**
 * Apply catch-delta learning from pending catch + count maps.
 */
function processCatchDelta({
  pendingCatch,
  previousItemCounts,
  currentItems,
  ingestLearned,
  mainCatalogLookup,
}) {
  const discovery = {
    lastPendingCatchName: null,
    lastInventoryDelta: null,
    learnedMappings: [],
    pendingLowConfidenceMappings: [],
    rejectedEvents: [],
  };

  const pending = sanitisePendingCatch(pendingCatch);
  if (!pending) {
    if (pendingCatch) {
      discovery.rejectedEvents.push({ reason: 'invalid_catch_name', raw: pendingCatch });
    }
    return discovery;
  }
  discovery.lastPendingCatchName = pending;

  const prev = sanitiseCountMap(previousItemCounts);
  const cur = buildItemCountsFromItems(currentItems);
  const increased = computeIncreasedIds(prev, cur);
  discovery.lastInventoryDelta = { increased, previousCounts: prev, currentCounts: cur };

  if (increased.length === 0) {
    discovery.rejectedEvents.push({ reason: 'no_inventory_delta', catchName: pending.fishName });
    return discovery;
  }

  if (increased.length === 1) {
    const inc = increased[0];
    const main = mainCatalogLookup ? mainCatalogLookup(inc.itemId) : null;
    if (main && main.category && main.category !== 'fish' && (main.category === 'rod' || main.category === 'rods' || main.category === 'bait')) {
      discovery.rejectedEvents.push({
        reason: 'existing_non_fish_protected',
        itemId: inc.itemId,
        existingName: main.name,
        catchName: pending.fishName,
      });
      return discovery;
    }

    const mapping = {
      itemId: inc.itemId,
      name: pending.fishName,
      category: 'fish',
      source: 'catch_delta_high_confidence',
      confidence: 1.0,
      proof: {
        beforeAmount: inc.beforeAmount,
        afterAmount: inc.afterAmount,
        delta: inc.delta,
        catchName: pending.fishName,
        catchSource: pending.source,
      },
    };
    const ingestResult = ingestLearned(mapping);
    const learnedItem = {
      itemId: inc.itemId,
      learnedName: pending.fishName,
      source: 'catch_delta_high_confidence',
      beforeAmount: inc.beforeAmount,
      afterAmount: inc.afterAmount,
      publicEligible: !!(ingestResult.entry && ingestResult.entry.publicEligible),
      ingest: ingestResult,
    };
    if (ingestResult.updated !== false) {
      discovery.learnedMappings.push(learnedItem);
    } else {
      discovery.rejectedEvents.push({
        reason: ingestResult.reason || 'ingest_failed',
        itemId: inc.itemId,
        catchName: pending.fishName,
      });
    }
    return discovery;
  }

  for (const inc of increased) {
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
  return discovery;
}

function buildNameCatalogDiscoveryForDebug(sessionDiscovery, learnedCatalog) {
  const learned = learnedCatalog.getAllMappings();
  const unresolved = [];
  for (const m of learned) {
    if (!m.publicEligible) {
      unresolved.push({ itemId: m.itemId, reason: 'low_confidence_pending' });
    }
  }
  return {
    lastPendingCatchName: sessionDiscovery?.lastPendingCatchName || null,
    lastInventoryDelta: sessionDiscovery?.lastInventoryDelta || null,
    learnedMappings: sessionDiscovery?.learnedMappings || [],
    pendingLowConfidenceMappings: sessionDiscovery?.pendingLowConfidenceMappings || [],
    rejectedEvents: sessionDiscovery?.rejectedEvents || [],
    persistentLearnedCount: learned.length,
    persistentHighConfidence: learned.filter((e) => e.publicEligible).map((e) => ({
      itemId: e.itemId,
      learnedName: e.name,
      source: e.source,
      publicEligible: true,
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
};
