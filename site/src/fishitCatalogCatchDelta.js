'use strict';
/**
 * Catch notification + inventory delta name catalog discovery (BLOCKER10M/10O/10P/10Z16).
 */

const learnedFishCatalog = require('./fishitLearnedFishCatalog');
const globalFishCatalog = require('./fishitGlobalFishItemCatalog');
const catchNameParser = require('./fishitCatchNameParser');
const nameOnlyCatalog = require('./fishitNameOnlyCatalog');
const rarityLabels = require('./fishitRarityLabels');

const CATCH_STALE_SECONDS = Number(process.env.FISHIT_CATCH_STALE_SECONDS || 180);
const CATCH_BINDING_WINDOW_MS = Number(process.env.FISHIT_CATCH_BINDING_WINDOW_MS || 120000);
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
    uploadId: raw?.uploadId || raw?.eventId || null,
  };
}

function isCatchStale(pending) {
  if (!pending || !pending.detectedAt) return false;
  const t = Date.parse(pending.detectedAt);
  if (!Number.isFinite(t)) return false;
  return (Date.now() - t) > CATCH_STALE_SECONDS * 1000;
}

function isWithinCatchBindingWindow(pending) {
  if (!pending?.detectedAt) return true;
  const t = Date.parse(pending.detectedAt);
  if (!Number.isFinite(t)) return true;
  return (Date.now() - t) <= CATCH_BINDING_WINDOW_MS;
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
  const allIds = new Set([...Object.keys(prev), ...Object.keys(cur)]);
  for (const id of allIds) {
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

function partitionIncreasedDeltas(increased, mainCatalogLookup, currentItems) {
  const fishLike = [];
  const ignoredNonFish = [];
  for (const inc of increased || []) {
    if (isKnownNonFishItemId(inc.itemId, mainCatalogLookup, currentItems)) {
      ignoredNonFish.push({
        ...inc,
        ignoredReason: 'known_non_fish',
      });
    } else {
      fishLike.push(inc);
    }
  }
  return { fishLike, ignoredNonFish };
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

function _submitNameOnlyGlobalEvidence(ctx, pending, parsed, discovery) {
  if (!ctx || ctx.enabled === false) return null;
  try {
    const result = globalFishCatalog.submitNameOnlyEvidence({
      userId: ctx.userId,
      userIdHash: ctx.userIdHash || globalFishCatalog.hashContributorId(ctx.userId),
      gameId: ctx.gameId || null,
      placeId: ctx.placeId || null,
      gameVersion: ctx.gameVersion || null,
      evidenceSourceMode: ctx.evidenceSourceMode || 'api_simulation',
      sessionKey: ctx.sessionKey || null,
      fishNameCandidate: pending.baseFishName || pending.fishName,
      displayName: pending.displayName,
      mutation: pending.mutation,
      weightKg: pending.weightKg,
      rarityCandidate: pending.rarityCandidate || parsed.rarityCandidate,
      source: pending.source || 'catch_notification',
      sourceText: parsed.rawText || pending.rawText,
      uploadId: pending.uploadId || null,
      detectedAt: pending.detectedAt || null,
    });
    discovery.globalEvidence = result;
    discovery.liveCatchAccepted = !!(result && result.accepted);
    discovery.liveCatchAcceptReason = result?.reason || 'catch_notification_pending_binding';
    discovery.liveCatchPendingObservationId = result?.observationId || null;
    discovery.liveCatchGlobalEvidenceStatus = result?.decision || (result?.pending ? 'pending' : null);
    discovery.promotionDecision = result?.decision || 'pending';
    discovery.promotionReason = result?.reason || 'awaiting_inventory_row_binding';
    discovery.nextExpectedAction = 'catch detected but waiting for inventory row binding';
    if (result?.accepted) {
      discovery.pendingCatchObservations = [{
        observationId: result.observationId,
        baseFishName: pending.baseFishName || pending.fishName,
        displayName: pending.displayName,
        status: 'pending_binding',
        source: 'catch_notification',
        detectedAt: pending.detectedAt,
      }];
    }
    return result;
  } catch (_) {
    discovery.liveCatchAccepted = false;
    discovery.liveCatchAcceptReason = 'name_only_evidence_error';
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

function _processSingleFishDelta({
  inc,
  pending,
  parsed,
  discovery,
  ingestLearned,
  mainCatalogLookup,
  currentItems,
  globalContext,
}) {
  const main = mainCatalogLookup ? mainCatalogLookup(inc.itemId) : null;
  if (main && main.category && main.category !== 'fish'
      && (main.category === 'rod' || main.category === 'rods' || main.category === 'bait')) {
    discovery.rejectedEvents.push({
      reason: 'known_non_fish',
      itemId: inc.itemId,
      existingName: main.name,
      catchName: pending.fishName,
    });
    _submitNameOnlyGlobalEvidence(globalContext, pending, parsed, discovery);
    return discovery;
  }

  const nameValidation = nameOnlyCatalog.validateFishName(pending.baseFishName || pending.fishName);
  const learnMeta = resolveLearnSource(pending, 1, nameValidation, globalContext);
  discovery.promotionDecision = learnMeta.promotionDecision;
  discovery.promotionReason = learnMeta.promotionReason;
  discovery.liveCatchAccepted = true;
  discovery.liveCatchAcceptReason = learnMeta.promotionReason;
  discovery.nextExpectedAction = learnMeta.promotionDecision === 'confirmed'
    ? 'public_card_when_snapshot_backed'
    : 'awaiting_additional_evidence';

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
  discovery.liveCatchGlobalEvidenceStatus = discovery.globalEvidence?.decision || null;
  discovery.liveCatchPendingObservationId = discovery.globalEvidence?.observationId || null;

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
    discovery.catchToSnapshotBindingProof = {
      bound: true,
      status: 'bound_via_inventory_delta',
      itemId: inc.itemId,
      reason: 'single_fish_delta_after_catch',
    };
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

function fingerprintSnapshotItem(it) {
  if (!it) return null;
  if (it.replionUuid) return `uuid:${String(it.replionUuid)}`;
  const id = it.itemId != null ? String(it.itemId) : '';
  const meta = it.metadataFishId || it.metadataFishName || '';
  if (id && meta) return `meta:${id}:${meta}`;
  if (id) return `id:${id}:${it.amount || 1}`;
  return null;
}

function isFishLikeSnapshotRow(it, mainCatalogLookup) {
  if (!it || it.itemId == null) return false;
  if (isKnownNonFishItemId(it.itemId, mainCatalogLookup, [it])) return false;
  const cat = String(it.category || '').toLowerCase();
  if (cat === 'fish') return true;
  if (it.replionUuid) return true;
  if (it.metadataFishName || it.metadataFishId) return true;
  const main = mainCatalogLookup ? mainCatalogLookup(it.itemId) : null;
  return !!(main && main.category === 'fish');
}

function attemptCatchSnapshotBinding({
  pendingCatch,
  previousItems,
  currentItems,
  mainCatalogLookup,
  ingestLearned,
  globalContext,
  existingDiscovery,
}) {
  const proof = {
    attempted: false,
    bound: false,
    status: 'not_attempted',
    newRows: [],
    reason: null,
    nextExpectedAction: null,
  };
  const pending = sanitisePendingCatch(pendingCatch);
  if (!pending) {
    proof.reason = 'no_pending_catch';
    return proof;
  }
  if (isCatchStale(pending)) {
    proof.reason = 'stale_catch';
    return proof;
  }
  if (!isWithinCatchBindingWindow(pending)) {
    proof.reason = 'catch_binding_window_expired';
    return proof;
  }
  proof.attempted = true;
  proof.catchName = pending.baseFishName || pending.fishName;
  const prevFp = new Set((previousItems || []).map(fingerprintSnapshotItem).filter(Boolean));
  const newRows = (currentItems || []).filter((it) => {
    const fp = fingerprintSnapshotItem(it);
    return fp && !prevFp.has(fp) && isFishLikeSnapshotRow(it, mainCatalogLookup);
  });
  proof.newRows = newRows.map((it) => ({
    itemId: it.itemId,
    replionUuid: it.replionUuid || null,
    metadataFishName: it.metadataFishName || null,
    metadataFishId: it.metadataFishId || null,
    category: it.category || null,
  }));

  if (newRows.length === 0) {
    proof.status = 'waiting_for_inventory_row_binding';
    proof.reason = 'catch detected but waiting for inventory row binding';
    proof.nextExpectedAction = proof.reason;
    return proof;
  }

  if (newRows.length > 1) {
    proof.status = 'ambiguous_new_rows';
    proof.reason = 'multiple_new_fish_rows_after_catch';
    proof.nextExpectedAction = 'awaiting_single_strong_row_binding';
    return proof;
  }

  const row = newRows[0];
  const nameValidation = nameOnlyCatalog.validateFishName(pending.baseFishName || pending.fishName);
  const hasNewUuid = !!row.replionUuid;
  const hasMetadata = !!(row.metadataFishName || row.metadataFishId);
  const strongEvidence = hasNewUuid || hasMetadata;
  if (!strongEvidence) {
    proof.status = 'weak_row_evidence';
    proof.reason = 'new_row_without_uuid_or_metadata';
    proof.nextExpectedAction = 'catch detected but waiting for inventory row binding';
    return proof;
  }

  const learnMeta = {
    source: 'catch_snapshot_binding',
    confidence: hasNewUuid ? 0.9 : 0.75,
    promotionDecision: nameValidation.nameKnown ? 'confirmed' : 'pending',
    promotionReason: hasNewUuid ? 'new_uuid_after_catch' : 'new_metadata_after_catch',
  };
  const mapping = {
    itemId: row.itemId,
    name: pending.baseFishName || pending.fishName,
    displayName: pending.displayName || pending.fishName,
    mutation: pending.mutation || null,
    weightKg: pending.weightKg != null ? pending.weightKg : null,
    category: 'fish',
    source: learnMeta.source,
    confidence: learnMeta.confidence,
    proof: {
      catchName: pending.baseFishName || pending.fishName,
      catchAt: pending.detectedAt,
      replionUuid: row.replionUuid || null,
      bindingEvidence: learnMeta.promotionReason,
    },
  };
  const ingestResult = ingestLearned(mapping);
  const globalEv = _submitGlobalEvidence(globalContext, {
    itemId: row.itemId,
    fishNameCandidate: pending.baseFishName || pending.fishName,
    displayName: pending.displayName,
    mutation: pending.mutation,
    weightKg: pending.weightKg,
    source: pending.source,
    sourceText: pending.rawText,
    deltaAmount: 1,
    cleanSingleDelta: true,
    confidenceSignal: learnMeta.promotionReason,
  });

  proof.bound = ingestResult.updated !== false;
  proof.status = proof.bound ? 'bound' : 'binding_rejected';
  proof.itemId = row.itemId;
  proof.replionUuid = row.replionUuid || null;
  proof.globalEvidence = globalEv;
  proof.ingest = ingestResult;
  proof.reason = learnMeta.promotionReason;
  proof.nextExpectedAction = proof.bound ? 'public_card_when_snapshot_backed' : 'binding_failed';

  if (existingDiscovery && proof.bound) {
    existingDiscovery.catchToSnapshotBindingProof = proof;
    existingDiscovery.liveCatchAcceptReason = 'catch_bound_to_inventory_row';
    existingDiscovery.liveCatchGlobalEvidenceStatus = globalEv?.decision || 'pending';
    existingDiscovery.nextExpectedAction = proof.nextExpectedAction;
    if (ingestResult.entry?.publicEligible) {
      existingDiscovery.learnedMappings.push({
        itemId: row.itemId,
        learnedName: pending.baseFishName || pending.fishName,
        displayName: pending.displayName,
        source: learnMeta.source,
        publicEligible: true,
        promotionDecision: learnMeta.promotionDecision,
        promotionReason: learnMeta.promotionReason,
        ingest: ingestResult,
      });
    }
  }
  return proof;
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
    ignoredDeltaProof: [],
    learnedMappings: [],
    pendingLowConfidenceMappings: [],
    pendingCatchObservations: [],
    rejectedEvents: [],
    globalEvidence: null,
    liveCatchAccepted: false,
    liveCatchAcceptReason: null,
    liveCatchPendingObservationId: null,
    liveCatchGlobalEvidenceStatus: null,
    catchToSnapshotBindingProof: null,
    nextExpectedAction: null,
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
    discovery.liveCatchAcceptReason = 'upload_failed';
    return discovery;
  }

  if (!pendingCatch) {
    discovery.rejectedEvents.push({ reason: 'no_catch_name' });
    return discovery;
  }

  const pending = sanitisePendingCatch(pendingCatch);
  const catchNameForValidation = pending?.baseFishName || pending?.fishName
    || parsed.baseFishName || parsed.fishNameCandidate;
  const invalidName = !pending || rarityLabels.isBlockedLearnName(catchNameForValidation);
  const invalidReason = !catchNameForValidation
    ? (parsed.rarityCandidate && rarityLabels.isRarityLabel(parsed.rarityCandidate)
      ? 'name_is_rarity_label' : 'no_valid_fish_name_candidate')
    : (rarityLabels.isRarityLabel(catchNameForValidation)
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

  const hasValidCatch = !!(pending && !invalidName);
  const prev = sanitiseCountMap(previousItemCounts);
  const cur = buildItemCountsFromItems(currentItems);
  discovery.previousInventoryCounts = prev;
  discovery.currentInventoryCounts = cur;

  if (!prev || Object.keys(prev).length === 0) {
    if (hasValidCatch) {
      _submitNameOnlyGlobalEvidence(globalContext, pending, parsed, discovery);
      discovery.rejectedEvents.push({
        reason: 'no_previous_inventory',
        catchName: pending.fishName,
        note: 'catch_notification_accepted_pending_binding',
      });
      return discovery;
    }
    discovery.rejectedEvents.push({
      reason: 'no_previous_inventory',
      catchName: pending && pending.fishName,
    });
    return discovery;
  }

  const increased = computeIncreasedIds(prev, cur);
  const { fishLike, ignoredNonFish } = partitionIncreasedDeltas(
    increased,
    mainCatalogLookup,
    currentItems,
  );
  discovery.lastInventoryDelta = {
    increased,
    fishLike,
    ignoredNonFish,
    previousCounts: prev,
    currentCounts: cur,
  };
  discovery.deltaCandidates = fishLike.length ? fishLike : increased;
  discovery.ignoredDeltaProof = ignoredNonFish;

  if (ignoredNonFish.length > 0) {
    for (const ign of ignoredNonFish) {
      _recordPipeline(globalContext, {
        eventType: 'live_delta_ignored_non_fish',
        itemId: ign.itemId,
        reason: ign.ignoredReason,
        fishName: pending && pending.fishName,
      });
    }
  }

  if (fishLike.length > 0) {
    _recordPipeline(globalContext, {
      eventType: 'live_delta_detected',
      itemId: fishLike.length === 1 ? fishLike[0].itemId : null,
      reason: fishLike.length === 1 ? 'single_fish_delta' : 'multiple_fish_delta',
      fishName: pending && pending.fishName,
    });
  } else if (increased.length === 0) {
    _recordPipeline(globalContext, {
      eventType: 'live_delta_detected',
      itemId: null,
      reason: 'no_delta',
      fishName: pending && pending.fishName,
    });
  }

  if (invalidName) {
    if (fishLike.length === 1) {
      const badName = parsed.rarityCandidate || parsed.rawText || parsed.fishNameCandidate;
      learnedFishCatalog.blockEntry(fishLike[0].itemId, badName, invalidReason, {
        sourceText: parsed.rawText,
        parserDecision: parsed.parserDecision,
      });
      discovery.globalEvidence = _submitGlobalEvidence(globalContext, {
        itemId: fishLike[0].itemId,
        fishNameCandidate: badName,
        rarityCandidate: parsed.rarityCandidate,
        source: parsed.source,
        sourceText: parsed.rawText,
        deltaAmount: fishLike[0].delta,
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
      itemId: fishLike.length === 1 ? fishLike[0].itemId : undefined,
    });
    if (fishLike.length > 1) {
      discovery.rejectedEvents.push({
        reason: 'multiple_delta_candidates',
        catchName: pending && pending.fishName,
        candidates: fishLike.map((i) => i.itemId),
      });
    }
    return discovery;
  }

  if (fishLike.length === 1) {
    return _processSingleFishDelta({
      inc: fishLike[0],
      pending,
      parsed,
      discovery,
      ingestLearned,
      mainCatalogLookup,
      currentItems,
      globalContext,
    });
  }

  if (fishLike.length > 1) {
    for (const inc of fishLike) {
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
      candidates: fishLike.map((i) => i.itemId),
    });
    if (hasValidCatch) {
      _submitNameOnlyGlobalEvidence(globalContext, pending, parsed, discovery);
    }
    return discovery;
  }

  if (hasValidCatch) {
    if (increased.length === 0) {
      discovery.rejectedEvents.push({
        reason: 'no_fish_delta',
        catchName: pending.fishName,
        note: 'catch_notification_accepted_pending_binding',
      });
    } else if (ignoredNonFish.length > 0) {
      discovery.rejectedEvents.push({
        reason: 'non_fish_delta_only',
        catchName: pending.fishName,
        note: 'catch_notification_accepted_pending_binding',
      });
    }
    _submitNameOnlyGlobalEvidence(globalContext, pending, parsed, discovery);
    return discovery;
  }

  if (increased.length === 0) {
    discovery.rejectedEvents.push({
      reason: 'no_delta',
      catchName: pending && pending.fishName,
    });
  } else if (ignoredNonFish.length > 0) {
    discovery.rejectedEvents.push({
      reason: 'known_non_fish_only',
      itemId: ignoredNonFish[0].itemId,
      catchName: pending && pending.fishName,
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
  const globalEv = sessionDiscovery?.globalEvidence || null;
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
    ignoredDeltaProof: sessionDiscovery?.ignoredDeltaProof || [],
    learnedMappings: sessionDiscovery?.learnedMappings || [],
    pendingLowConfidenceMappings: sessionDiscovery?.pendingLowConfidenceMappings || [],
    pendingCatchObservations: sessionDiscovery?.pendingCatchObservations || [],
    rejectedEvents: sessionDiscovery?.rejectedEvents || [],
    liveCatchAccepted: sessionDiscovery?.liveCatchAccepted ?? null,
    liveCatchAcceptReason: sessionDiscovery?.liveCatchAcceptReason ?? null,
    liveCatchPendingObservationId: sessionDiscovery?.liveCatchPendingObservationId ?? null,
    liveCatchGlobalEvidenceStatus: sessionDiscovery?.liveCatchGlobalEvidenceStatus ?? null,
    catchToSnapshotBindingProof: sessionDiscovery?.catchToSnapshotBindingProof || null,
    nextExpectedAction: sessionDiscovery?.nextExpectedAction ?? null,
    liveGlobalEvidenceProof: globalEv ? {
      accepted: globalEv.accepted === true,
      rejected: globalEv.rejected === true,
      pending: globalEv.pending === true || globalEv.decision === 'pending',
      reason: globalEv.reason || null,
      observationId: globalEv.observationId || null,
      conflictId: globalEv.conflictId || null,
      decision: globalEv.decision || null,
    } : null,
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

function buildLiveCatchEvidenceResponse(discovery) {
  if (!discovery) return null;
  const ge = discovery.globalEvidence || null;
  let speciesEvidenceProof = null;
  if (ge?.baseFishName || discovery.lastCatchParsed?.baseFishName) {
    try {
      const baseName = ge?.baseFishName || discovery.lastCatchParsed.baseFishName;
      speciesEvidenceProof = require('./fishitGlobalCatalogService')
        .buildGlobalSpeciesEvidenceProof(baseName);
    } catch (_) { /* optional */ }
  }
  return {
    liveCatchAccepted: discovery.liveCatchAccepted === true,
    liveCatchAcceptReason: discovery.liveCatchAcceptReason || null,
    liveCatchPendingObservationId: discovery.liveCatchPendingObservationId || null,
    liveCatchGlobalEvidenceStatus: discovery.liveCatchGlobalEvidenceStatus || null,
    accepted: ge?.accepted === true,
    rejected: ge?.rejected === true,
    pending: ge?.pending === true || ge?.decision === 'pending',
    reason: ge?.reason || discovery.liveCatchAcceptReason || null,
    observationId: ge?.observationId || discovery.liveCatchPendingObservationId || null,
    conflictId: ge?.conflictId || null,
    decision: ge?.decision || discovery.liveCatchGlobalEvidenceStatus || null,
    nextExpectedAction: discovery.nextExpectedAction || null,
    speciesEvidenceProof,
    itemIdMappingStatus: speciesEvidenceProof?.hasItemIdMapping ? 'bound' : 'pending',
  };
}

module.exports = {
  normalizeCatchFishName: catchNameParser.normalizeCatchFishName,
  sanitisePendingCatch,
  buildItemCountsFromItems,
  sanitiseCountMap,
  computeIncreasedIds,
  partitionIncreasedDeltas,
  processCatchDelta,
  attemptCatchSnapshotBinding,
  buildNameCatalogDiscoveryForDebug,
  buildLiveCatchEvidenceResponse,
  unresolvedReasonForItem,
  CATCH_STALE_SECONDS,
  CATCH_BINDING_WINDOW_MS,
  KNOWN_NON_FISH_IDS: learnedFishCatalog.KNOWN_NON_FISH_IDS,
};
