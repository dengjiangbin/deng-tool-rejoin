'use strict';
/**
 * Catch notification + inventory delta name catalog discovery (BLOCKER10M/10O/10P).
 */

const learnedFishCatalog = require('./fishitLearnedFishCatalog');
const globalFishCatalog = require('./fishitGlobalFishItemCatalog');
const catchNameParser = require('./fishitCatchNameParser');
const nameOnlyCatalog = require('./fishitNameOnlyCatalog');
const rarityLabels = require('./fishitRarityLabels');

const CATCH_STALE_SECONDS = Number(process.env.FISHIT_CATCH_STALE_SECONDS || 180);
const VERIFIED_CATCH_SOURCES = new Set(['catch_notification', 'catch_event']);

function sanitisePendingCatch(raw) {
  const parsed = catchNameParser.parseCatchInput({
    fishName: raw?.rawText || raw?.fishName || raw?.name,
    rawText: raw?.rawText,
    source: raw?.source,
    detectedAt: raw?.detectedAt,
  });
  if (!parsed.baseFishName && !parsed.fishNameCandidate && !raw?.baseFishName && !raw?.fishName) return null;
  const baseFishName = raw?.baseFishName || parsed.baseFishName || parsed.fishNameCandidate || raw?.fishName;
  const displayName = raw?.displayName || parsed.displayName || baseFishName;
  const mutation = raw?.mutation != null ? raw.mutation : (parsed.mutation || null);
  const weightKg = raw?.weightKg != null ? raw.weightKg : parsed.weightKg;
  return {
    fishName: baseFishName,
    baseFishName,
    displayName,
    mutation,
    weightKg: weightKg != null ? weightKg : null,
    rarityCandidate: parsed.rarityCandidate,
    detectedAt: parsed.detectedAt,
    source: parsed.source,
    rawText: parsed.rawText,
    parserDecision: parsed.parserDecision,
    confidence: parsed.confidence,
  };
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

function itemCategoryFromCurrent(currentItems, itemId) {
  if (!Array.isArray(currentItems)) return null;
  const id = String(itemId);
  const hit = currentItems.find((it) => it && String(it.itemId) === id);
  return hit ? String(hit.category || '').toLowerCase() : null;
}

function isKnownNonFishItemId(itemId, mainCatalogLookup, currentItems) {
  if (learnedFishCatalog.isKnownNonFishId(itemId)) return true;
  const curCat = itemCategoryFromCurrent(currentItems, itemId);
  if (curCat === 'rod' || curCat === 'rods' || curCat === 'bait') return true;
  const main = mainCatalogLookup ? mainCatalogLookup(itemId) : null;
  if (!main) return false;
  const cat = String(main.category || '').toLowerCase();
  return cat === 'rod' || cat === 'rods' || cat === 'bait' || cat === 'items';
}

function resolveLearnSource(pending, increasedCount, nameValidation, globalContext) {
  const verifiedCatch = VERIFIED_CATCH_SOURCES.has(pending.source);
  const nameKnown = !!(nameValidation && nameValidation.nameKnown);
  const isLiveRoblox = globalContext?.evidenceSourceMode === 'live_roblox';
  if (increasedCount === 1 && verifiedCatch && isLiveRoblox) {
    return {
      source: nameKnown ? 'catch_delta_high_confidence' : 'live_roblox_catch_delta',
      confidence: nameKnown ? 1.0 : 0.85,
      promotionDecision: 'confirmed',
      promotionReason: nameKnown
        ? 'verified_name_single_delta'
        : 'live_roblox_single_delta_public',
    };
  }
  if (increasedCount === 1 && verifiedCatch && nameKnown) {
    return {
      source: 'catch_delta_high_confidence',
      confidence: 1.0,
      promotionDecision: 'confirmed',
      promotionReason: 'verified_name_single_delta',
    };
  }
  if (increasedCount === 1) {
    return {
      source: 'catch_delta_pending',
      confidence: 0.5,
      promotionDecision: 'pending',
      promotionReason: nameKnown ? 'awaiting_second_observation' : 'name_not_in_catalog',
    };
  }
  return {
    source: 'catch_delta_low_confidence',
    confidence: 0.3,
    promotionDecision: 'pending',
    promotionReason: 'ambiguous_delta',
  };
}

/**
 * Apply catch-delta learning from pending catch + count maps.
 */
function _submitGlobalEvidence(ctx, payload) {
  if (!ctx || ctx.enabled === false) return null;
  try {
    return globalFishCatalog.submitEvidence({
      userId: ctx.userId,
      userIdHash: ctx.userIdHash || globalFishCatalog.hashContributorId(ctx.userId),
      gameId: ctx.gameId || null,
      placeId: ctx.placeId || null,
      gameVersion: ctx.gameVersion || null,
      evidenceSourceMode: ctx.evidenceSourceMode || 'api_simulation',
      sessionKey: ctx.sessionKey || null,
      ...payload,
    });
  } catch (_) {
    return null;
  }
}

function _recordPipeline(ctx, evt) {
  if (!ctx || ctx.enabled === false) return;
  try {
    globalFishCatalog.recordPipelineEvent({
      evidenceSourceMode: ctx.evidenceSourceMode || 'api_simulation',
      sessionKey: ctx.sessionKey || null,
      ...evt,
    });
  } catch (_) { /* optional */ }
}

function processCatchDelta({
  pendingCatch,
  previousItemCounts,
  currentItems,
  ingestLearned,
  mainCatalogLookup,
  uploadFailed,
  globalContext,
}) {
  const parsed = catchNameParser.parseCatchInput({
    rawText: pendingCatch?.rawText,
    fishName: pendingCatch?.rawText || pendingCatch?.fishName || pendingCatch?.name,
    source: pendingCatch?.source,
    detectedAt: pendingCatch?.detectedAt,
  });
  const discovery = {
    lastPendingCatchName: null,
    lastCatchAt: null,
    lastFishNameCandidate: parsed.baseFishName || parsed.fishNameCandidate,
    lastBaseFishNameCandidate: parsed.baseFishName || parsed.fishNameCandidate,
    lastDisplayNameCandidate: parsed.displayName,
    lastMutationCandidate: parsed.mutation,
    lastWeightKg: parsed.weightKg,
    lastRarityCandidate: parsed.rarityCandidate,
    lastParserSource: parsed.source,
    lastParserDecision: parsed.parserDecision,
    lastParserRawText: parsed.rawText,
    lastCatchParsed: {
      rawText: parsed.rawText,
      baseFishName: parsed.baseFishName || parsed.fishNameCandidate,
      displayName: parsed.displayName,
      mutation: parsed.mutation,
      rarity: parsed.rarityCandidate,
      weightKg: parsed.weightKg,
      parserDecision: parsed.parserDecision,
    },
    promotionDecision: null,
    promotionReason: null,
    previousInventoryCounts: null,
    currentInventoryCounts: null,
    lastInventoryDelta: null,
    deltaCandidates: [],
    learnedMappings: [],
    pendingLowConfidenceMappings: [],
    rejectedEvents: [],
    globalEvidence: null,
    evidenceSourceMode: globalContext?.evidenceSourceMode || 'api_simulation',
    sessionKey: globalContext?.sessionKey || null,
  };

  if (parsed.rawText) {
    _recordPipeline(globalContext, {
      eventType: 'live_catch_text_seen',
      rawText: parsed.rawText,
      fishName: parsed.fishNameCandidate,
      rarity: parsed.rarityCandidate,
    });
    _recordPipeline(globalContext, {
      eventType: 'live_catch_parse_result',
      decision: parsed.parserDecision,
      reason: parsed.fishNameCandidate ? 'parsed_ok' : (parsed.parserDecision || 'parse_failed'),
      fishName: parsed.fishNameCandidate,
      rarity: parsed.rarityCandidate,
    });
  }

  if (uploadFailed) {
    discovery.rejectedEvents.push({ reason: 'upload_failed' });
    return discovery;
  }

  if (!pendingCatch) {
    discovery.rejectedEvents.push({ reason: 'no_catch_name' });
    return discovery;
  }

  const pending = sanitisePendingCatch(pendingCatch);
  const invalidName = !pending || rarityLabels.isBlockedLearnName(parsed.fishNameCandidate);
  const invalidReason = !parsed.fishNameCandidate
    ? (parsed.rarityCandidate && rarityLabels.isRarityLabel(parsed.rarityCandidate)
      ? 'name_is_rarity_label' : 'no_valid_fish_name_candidate')
    : (rarityLabels.isRarityLabel(parsed.fishNameCandidate)
      ? 'name_is_rarity_label' : 'name_is_status_label');
  if (pending) {
    discovery.lastPendingCatchName = pending;
    discovery.lastCatchAt = pending.detectedAt;
  }

  if (pending && isCatchStale(pending)) {
    discovery.rejectedEvents.push({
      reason: 'stale_catch',
      catchName: pending.fishName,
      detectedAt: pending.detectedAt,
    });
    return discovery;
  }

  const prev = sanitiseCountMap(previousItemCounts);
  const cur = buildItemCountsFromItems(currentItems);
  discovery.previousInventoryCounts = prev;
  discovery.currentInventoryCounts = cur;

  if (!prev || Object.keys(prev).length === 0) {
    discovery.rejectedEvents.push({
      reason: 'no_previous_inventory',
      catchName: pending && pending.fishName,
    });
    return discovery;
  }

  const increased = computeIncreasedIds(prev, cur);
  discovery.lastInventoryDelta = { increased, previousCounts: prev, currentCounts: cur };
  discovery.deltaCandidates = increased;
  if (increased.length > 0) {
    _recordPipeline(globalContext, {
      eventType: 'live_delta_detected',
      itemId: increased.length === 1 ? increased[0].itemId : null,
      reason: increased.length === 1 ? 'single_delta' : 'multiple_delta',
      fishName: pending && pending.fishName,
    });
  }

  if (increased.length === 0) {
    discovery.rejectedEvents.push({
      reason: 'no_delta',
      catchName: pending && pending.fishName,
    });
    return discovery;
  }

  if (invalidName) {
    if (increased.length === 1) {
      const badName = parsed.rarityCandidate || parsed.rawText || parsed.fishNameCandidate;
      learnedFishCatalog.blockEntry(increased[0].itemId, badName, invalidReason, {
        sourceText: parsed.rawText,
        parserDecision: parsed.parserDecision,
      });
      discovery.globalEvidence = _submitGlobalEvidence(globalContext, {
        itemId: increased[0].itemId,
        fishNameCandidate: badName,
        rarityCandidate: parsed.rarityCandidate,
        source: parsed.source,
        sourceText: parsed.rawText,
        deltaAmount: increased[0].delta,
        cleanSingleDelta: true,
      });
    }
    discovery.rejectedEvents.push({
      reason: invalidReason,
      raw: pendingCatch,
      rejectedLearnedName: parsed.rarityCandidate || parsed.rawText,
      rejectedReason: invalidReason,
      sourceText: parsed.rawText,
      rarityCandidate: parsed.rarityCandidate,
      parserDecision: parsed.parserDecision,
      itemId: increased.length === 1 ? increased[0].itemId : undefined,
    });
    if (increased.length > 1) {
      discovery.rejectedEvents.push({
        reason: 'multiple_delta_candidates',
        catchName: pending && pending.fishName,
        candidates: increased.map((i) => i.itemId),
      });
    }
    return discovery;
  }

  if (increased.length > 1) {
    for (const inc of increased) {
      if (isKnownNonFishItemId(inc.itemId, mainCatalogLookup, currentItems)) {
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
  if (isKnownNonFishItemId(inc.itemId, mainCatalogLookup, currentItems)) {
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

  const nameValidation = nameOnlyCatalog.validateFishName(pending.baseFishName || pending.fishName);
  const learnMeta = resolveLearnSource(pending, 1, nameValidation, globalContext);
  discovery.promotionDecision = learnMeta.promotionDecision;
  discovery.promotionReason = learnMeta.promotionReason;

  const mapping = {
    itemId: inc.itemId,
    name: pending.baseFishName || pending.fishName,
    displayName: pending.displayName || pending.fishName,
    mutation: pending.mutation || null,
    weightKg: pending.weightKg != null ? pending.weightKg : null,
    category: 'fish',
    source: learnMeta.source,
    confidence: learnMeta.confidence,
    proof: {
      beforeAmount: inc.beforeAmount,
      afterAmount: inc.afterAmount,
      delta: inc.delta,
      catchName: pending.baseFishName || pending.fishName,
      displayName: pending.displayName,
      mutation: pending.mutation,
      weightKg: pending.weightKg,
      catchSource: pending.source,
      catchAt: pending.detectedAt,
      rarityCandidate: pending.rarityCandidate,
      parserDecision: pending.parserDecision,
      nameValidated: nameValidation.nameKnown,
      validationReason: nameValidation.reason,
      promotionDecision: learnMeta.promotionDecision,
      promotionReason: learnMeta.promotionReason,
      evidenceSourceMode: globalContext?.evidenceSourceMode || 'api_simulation',
      evidenceSources: nameValidation.reason ? [nameValidation.reason] : [],
    },
  };
  const ingestResult = ingestLearned(mapping);
  discovery.globalEvidence = _submitGlobalEvidence(globalContext, {
    itemId: inc.itemId,
    fishNameCandidate: pending.rawText || pending.displayName || pending.fishName,
    displayName: pending.displayName,
    rarityCandidate: pending.rarityCandidate,
    mutation: pending.mutation,
    weightKg: pending.weightKg,
    source: pending.source,
    sourceText: parsed.rawText,
    deltaAmount: inc.delta,
    cleanSingleDelta: true,
    imageAssetIdCandidate: nameValidation.imageAssetId || null,
    imageUrlCandidate: nameValidation.imageUrl || null,
    confidenceSignal: learnMeta.promotionReason,
  });
  const learnedItem = {
    itemId: inc.itemId,
    learnedName: pending.baseFishName || pending.fishName,
    displayName: pending.displayName,
    mutation: pending.mutation,
    source: ingestResult.entry ? ingestResult.entry.source : learnMeta.source,
    beforeAmount: inc.beforeAmount,
    afterAmount: inc.afterAmount,
    publicEligible: !!(ingestResult.entry && ingestResult.entry.publicEligible),
    promotionDecision: ingestResult.promotionDecision || learnMeta.promotionDecision,
    promotionReason: ingestResult.promotionReason || learnMeta.promotionReason,
    observationCount: ingestResult.observationCount,
    nameValidated: nameValidation.nameKnown,
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

  if (ingestResult.reason === 'name_is_rarity_label' || ingestResult.reason === 'name_is_status_label'
      || ingestResult.reason === 'blocked_history' || ingestResult.reason === 'name_conflict') {
    discovery.rejectedEvents.push({
      reason: ingestResult.reason,
      itemId: inc.itemId,
      catchName: pending.fishName,
      rejectedLearnedName: ingestResult.rejectedLearnedName || pending.fishName,
      conflictNames: ingestResult.conflictNames,
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
      promotionDecision: learnedItem.promotionDecision,
      promotionReason: learnedItem.promotionReason,
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
      unresolved.push({
        itemId: m.itemId,
        name: m.name,
        reason: 'low_confidence_pending',
        observationCount: m.proof && m.proof.observationCount,
      });
    }
  }
  const pendingCatch = sessionDiscovery?.lastPendingCatchName || sessionData?.lastPendingCatchName || null;
  return {
    lastPendingCatchName: pendingCatch,
    lastCatchAt: sessionDiscovery?.lastCatchAt || (pendingCatch && pendingCatch.detectedAt) || null,
    lastFishNameCandidate: sessionDiscovery?.lastFishNameCandidate ?? null,
    lastRarityCandidate: sessionDiscovery?.lastRarityCandidate ?? null,
    lastParserSource: sessionDiscovery?.lastParserSource ?? null,
    lastParserDecision: sessionDiscovery?.lastParserDecision ?? null,
    promotionDecision: sessionDiscovery?.promotionDecision ?? null,
    promotionReason: sessionDiscovery?.promotionReason ?? null,
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
  normalizeCatchFishName: catchNameParser.normalizeCatchFishName,
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
