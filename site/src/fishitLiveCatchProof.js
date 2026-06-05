'use strict';
/**
 * BLOCKER10R — live Roblox catch proof helpers.
 * Distinguishes live_roblox evidence from simulation/test fixtures.
 */

const catalogStore = require('./fishitCatalogStore');
const fishCatalog = require('./fishitFishCatalog');

const EVIDENCE_SOURCE_MODES = new Set([
  'live_roblox',
  'api_simulation',
  'test_fixture',
  'admin_seed',
  'static_seed',
]);

const KNOWN_SEED_ITEM_IDS = new Set(['68', '70', '71', '117', '119']);

const TARGET_UNRESOLVED_ITEM_IDS = new Set([
  '196', '72', '353', '74', '114', '112', '198', '67', '65', '115', '197',
]);

function isKnownSeedItemId(itemId) {
  return KNOWN_SEED_ITEM_IDS.has(String(itemId || '').trim());
}

function isTargetUnresolvedItemId(itemId) {
  return TARGET_UNRESOLVED_ITEM_IDS.has(String(itemId || '').trim());
}

function resolveEvidenceSourceMode(body) {
  const b = body || {};
  const pending = b.pendingCatchName || b.pendingCatch || {};
  const explicit = b.evidenceSourceMode || pending.evidenceSourceMode;
  if (explicit && EVIDENCE_SOURCE_MODES.has(explicit)) return explicit;
  if (b._testFixture || b.testFixture || process.env.FISHIT_TEST_FIXTURE === '1') {
    return 'test_fixture';
  }
  if (b.clientOrigin === 'roblox_tracker') return 'live_roblox';
  if (b.trackerBuild && String(b.trackerBuild).includes('BLOCKER10R')) return 'live_roblox';
  if (b.adminSeed || b.source === 'admin_seed') return 'admin_seed';
  if (b.source === 'static_seed' || b.source === 'seed_confirmed') return 'static_seed';
  return 'api_simulation';
}

function getPreviousItemIdStatus(itemId, globalLookup) {
  const id = String(itemId || '').trim();
  if (!id) return 'unresolved';
  if (isKnownSeedItemId(id)) return 'confirmed';
  const fish = fishCatalog.lookupByItemId(id);
  if (fish && fish.category === 'fish') return 'confirmed';
  const global = globalLookup ? globalLookup(id) : null;
  if (global) {
    if (global.confidence === 'blocked') return 'blocked';
    if (global.confidence === 'conflict') return 'conflict';
    if (global.confidence === 'confirmed' && global.publicEligible) return 'confirmed';
    if (global.confidence === 'pending') return 'pending';
  }
  const main = catalogStore.lookupById(id);
  if (main && catalogStore.isFishCategory(main.category)
      && !catalogStore.isPlaceholderItemName(main.name, id)) {
    return 'confirmed';
  }
  return 'unresolved';
}

function pickPrimaryDeltaItemId(deltas) {
  const list = Array.isArray(deltas) ? deltas : [];
  if (list.length === 1) return String(list[0].itemId);
  const target = list.find((d) => isTargetUnresolvedItemId(d.itemId) && !isKnownSeedItemId(d.itemId));
  if (target) return String(target.itemId);
  const novel = list.find((d) => !isKnownSeedItemId(d.itemId));
  return novel ? String(novel.itemId) : (list[0] ? String(list[0].itemId) : null);
}

function buildNewUnresolvedBindingProof(discovery, sessionKey, globalLookup) {
  const deltas = discovery?.deltaCandidates
    || (discovery?.lastInventoryDelta && discovery.lastInventoryDelta.increased)
    || [];
  const itemId = pickPrimaryDeltaItemId(deltas);
  const previousStatus = itemId ? getPreviousItemIdStatus(itemId, globalLookup) : 'unresolved';
  const wasExistingSeed = itemId ? isKnownSeedItemId(itemId) : false;
  const wasPreviouslyKnown = wasExistingSeed || previousStatus === 'confirmed';
  const globalEv = discovery?.globalEvidence || null;
  const evidenceSourceMode = discovery?.evidenceSourceMode
    || globalEv?.evidenceSourceMode
    || null;

  let decision = discovery?.promotionDecision || globalEv?.decision || null;
  let reason = discovery?.promotionReason || globalEv?.reason || null;
  if (!reason && discovery?.rejectedEvents?.length) {
    reason = discovery.rejectedEvents[discovery.rejectedEvents.length - 1].reason;
  }
  if (deltas.length > 1 && !decision) {
    decision = 'rejected';
    reason = reason || 'multiple_delta_candidates';
  }

  const attempted = !!(discovery?.lastParserRawText || discovery?.lastFishNameCandidate
    || discovery?.lastPendingCatchName);

  const countsAsNewUnresolvedProof = !!(
    attempted
    && itemId
    && !wasExistingSeed
    && !wasPreviouslyKnown
    && evidenceSourceMode === 'live_roblox'
  );

  return {
    attempted,
    itemId,
    previousStatus,
    fishNameCandidate: discovery?.lastFishNameCandidate ?? null,
    rarityCandidate: discovery?.lastRarityCandidate ?? null,
    deltaCandidates: deltas,
    decision,
    reason,
    wasExistingSeed,
    wasPreviouslyKnown,
    publicEligible: !!(globalEv && globalEv.publicEligible),
    evidenceSourceMode,
    countsAsNewUnresolvedProof,
    sessionKey: sessionKey || null,
  };
}

function isLiveProofSuccess(proof) {
  if (!proof || !proof.countsAsNewUnresolvedProof) return false;
  if (proof.evidenceSourceMode !== 'live_roblox') return false;
  if (proof.wasExistingSeed) return false;
  if (proof.wasPreviouslyKnown) return false;
  return ['confirmed', 'pending', 'rejected', 'blocked', 'conflict'].includes(proof.decision);
}

function buildEvidenceSourceDebug(storeMeta, sessionDiscovery) {
  const mode = sessionDiscovery?.evidenceSourceMode || storeMeta?.lastEvidenceSourceMode || null;
  return {
    evidenceSourceMode: mode,
    liveRobloxEvidenceCount: storeMeta?.liveRobloxEvidenceCount || 0,
    simulationEvidenceCount: storeMeta?.simulationEvidenceCount || 0,
    testFixtureEvidenceCount: storeMeta?.testFixtureEvidenceCount || 0,
    lastLiveCatchAt: storeMeta?.lastLiveCatchAt || sessionDiscovery?.lastCatchAt || null,
    lastLiveCatchSessionKey: storeMeta?.lastLiveCatchSessionKey || null,
  };
}

module.exports = {
  EVIDENCE_SOURCE_MODES,
  KNOWN_SEED_ITEM_IDS,
  TARGET_UNRESOLVED_ITEM_IDS,
  isKnownSeedItemId,
  isTargetUnresolvedItemId,
  resolveEvidenceSourceMode,
  getPreviousItemIdStatus,
  pickPrimaryDeltaItemId,
  buildNewUnresolvedBindingProof,
  isLiveProofSuccess,
  buildEvidenceSourceDebug,
};
