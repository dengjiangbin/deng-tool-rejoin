'use strict';

const playerStatsStore = require('./fishitPlayerStats');

function boolFlag(value) {
  return value === true || value === 'true' || value === 1;
}

function extractClientSnapshotProof(body) {
  const fishItemCount = Number.isFinite(Number(body?.fishItemCount))
    ? Number(body.fishItemCount)
    : null;
  const stoneItemCount = Number.isFinite(Number(body?.stoneItemCount))
    ? Number(body.stoneItemCount)
    : null;
  const proof = body?.playerDataGameItemDbProof && typeof body.playerDataGameItemDbProof === 'object'
    ? body.playerDataGameItemDbProof
    : null;
  const inventoryCount = Number.isFinite(Number(proof?.playerDataInventoryCount))
    ? Number(proof.playerDataInventoryCount)
    : (Number.isFinite(Number(proof?.inventoryCount)) ? Number(proof.inventoryCount) : null);

  return {
    firstExecution: boolFlag(body?.firstExecution),
    replionReady: boolFlag(body?.replionReady) || boolFlag(body?.replionFound),
    leaderstatsReady: boolFlag(body?.leaderstatsReady),
    fishScanReady: boolFlag(body?.fishScanReady),
    stoneScanReady: boolFlag(body?.stoneScanReady),
    scanCompleted: boolFlag(body?.scanCompleted),
    scanError: body?.scanError ? String(body.scanError).slice(0, 240) : null,
    fishItemCount: fishItemCount != null ? fishItemCount : null,
    stoneItemCount: stoneItemCount != null ? stoneItemCount : null,
    playerDataInventoryCount: inventoryCount,
    payloadType: body?.payloadType || body?.type || null,
  };
}

function resolveLeaderstatsState(body, existing) {
  const proof = extractClientSnapshotProof(body);
  if (proof.leaderstatsReady) {
    return { ready: true, reason: 'client_proof' };
  }
  const incoming = playerStatsStore.enrichIncomingPlayerStats(body?.playerStats, {
    trackerBuild: body?.trackerBuild,
    playerStatsDebug: body?.playerStatsDebug,
    isLiveRoblox: body?.clientOrigin === 'roblox_tracker' || body?.evidenceSourceMode === 'live_roblox',
  });
  if (playerStatsStore.isTrustedPlayerStats(incoming)
    && playerStatsStore.hasPlayerStatValues(incoming)
    && incoming?.source !== 'missing') {
    return { ready: true, reason: 'trusted_player_stats' };
  }
  if (playerStatsStore.isTrustedPlayerStats(existing?.playerStats)
    && playerStatsStore.hasPlayerStatValues(existing.playerStats)) {
    return { ready: false, reason: 'preserved_existing_stats', preserved: true };
  }
  return { ready: false, reason: 'leaderstats_not_ready' };
}

function countRows(rows) {
  return Array.isArray(rows) ? rows.length : 0;
}

function evaluateSnapshotCompleteness(ctx) {
  const {
    body,
    existing,
    cleanItems,
    playerDataFishItems,
    playerDataStoneItems,
    parseStats,
    partialInfo,
    isHeartbeat,
    now,
  } = ctx;
  const proof = extractClientSnapshotProof(body);
  const fishCount = countRows(playerDataFishItems);
  const stoneCount = countRows(playerDataStoneItems);
  const uploadedFishCount = Array.isArray(playerDataFishItems)
    ? fishCount
    : (proof.fishItemCount != null ? proof.fishItemCount : countRows(body?.fishItems));
  const uploadedStoneCount = Array.isArray(playerDataStoneItems)
    ? stoneCount
    : (proof.stoneItemCount != null ? proof.stoneItemCount : countRows(body?.stoneItems));
  const inventoryCount = proof.playerDataInventoryCount;

  const baseFields = {
    firstSeenAt: existing?.firstSeenAt || now,
    firstFullSnapshotAt: existing?.firstFullSnapshotAt || null,
    lastFullSnapshotAt: existing?.lastFullSnapshotAt || null,
    hasLeaderstatsSnapshot: existing?.hasLeaderstatsSnapshot === true,
    hasFishSnapshot: existing?.hasFishSnapshot === true,
    hasStoneSnapshot: existing?.hasStoneSnapshot === true,
    snapshotComplete: existing?.snapshotComplete === true,
    snapshotCompletenessReason: existing?.snapshotCompletenessReason || 'awaiting_first_full_snapshot',
    blankPayloadRejected: false,
    provenEmptyInventory: existing?.provenEmptyInventory === true,
    inventoryReady: existing?.snapshotComplete === true,
    fishItemCount: uploadedFishCount,
    stoneItemCount: uploadedStoneCount,
    playerDataInventoryCount: inventoryCount,
  };

  if (isHeartbeat) {
    return {
      ...baseFields,
      payloadType: 'heartbeat',
      snapshotCompletenessReason: existing?.snapshotComplete
        ? 'heartbeat_only_snapshot_already_complete'
        : 'heartbeat_only_awaiting_full_snapshot',
      preserveExistingInventory: true,
      rejectBlankInventory: false,
      quarantineBlankInventory: false,
    };
  }

  const hasIdentity = !!(body?.username && Number(body?.userId) > 0);
  const leaderstats = resolveLeaderstatsState(body, existing);
  const replionReady = proof.replionReady
    || body?.inventorySource === 'playerdata_gameitemdb'
    || !!existing?.inventorySource;
  const scanCompleted = proof.scanCompleted
    || (body?.inventorySource === 'playerdata_gameitemdb'
      && body?.playerDataGameItemDbProof?.gameItemDbBuilt === true
      && inventoryCount != null);
  const fishScanReady = proof.fishScanReady || scanCompleted || uploadedFishCount > 0;
  const stoneScanReady = proof.stoneScanReady || scanCompleted || uploadedStoneCount > 0;

  const unresolvedInventoryItems = inventoryCount != null && inventoryCount > 0
    && uploadedFishCount === 0 && uploadedStoneCount === 0;
  const blankPayload = uploadedFishCount === 0 && uploadedStoneCount === 0
    && !scanCompleted
    && !(parseStats?.acceptedInstances > 0)
    && !(parseStats?.accepted > 0);
  const hasPriorGood = !!(
    existing?.snapshotComplete
    || existing?.lastGoodFishItems?.length
    || existing?.items?.length
    || existing?.playerDataFishItems?.length
    || existing?.playerDataStoneItems?.length
    || existing?.lastFullSnapshotAt
  );

  const rejectBlankInventory = blankPayload && !hasPriorGood && !scanCompleted;
  const quarantineBlankInventory = blankPayload && hasPriorGood && !scanCompleted;
  const preserveExistingInventory = rejectBlankInventory
    || quarantineBlankInventory
    || partialInfo?.isPartial === true
    || unresolvedInventoryItems;

  const hasLeaderstatsSnapshot = leaderstats.ready;
  const hasFishSnapshot = fishScanReady && (
    uploadedFishCount > 0
    || (scanCompleted && !unresolvedInventoryItems)
  );
  const hasStoneSnapshot = stoneScanReady && (
    uploadedStoneCount > 0
    || (scanCompleted && !unresolvedInventoryItems)
  );

  const provenEmptyInventory = scanCompleted
    && hasIdentity
    && replionReady
    && hasLeaderstatsSnapshot
    && uploadedFishCount === 0
    && uploadedStoneCount === 0
    && (inventoryCount == null || inventoryCount === 0)
    && !unresolvedInventoryItems;

  let snapshotComplete = hasIdentity
    && replionReady
    && hasLeaderstatsSnapshot
    && hasFishSnapshot
    && hasStoneSnapshot
    && scanCompleted
    && !preserveExistingInventory
    && !partialInfo?.isPartial;

  let payloadType = 'partial';
  if (snapshotComplete) payloadType = 'full_snapshot';
  else if (rejectBlankInventory || quarantineBlankInventory) payloadType = 'partial';

  let snapshotCompletenessReason = 'awaiting_full_snapshot';
  if (!hasIdentity) snapshotCompletenessReason = 'missing_identity';
  else if (!replionReady) snapshotCompletenessReason = 'replion_not_ready';
  else if (!leaderstats.ready) snapshotCompletenessReason = leaderstats.reason;
  else if (!fishScanReady) snapshotCompletenessReason = 'fish_scan_not_ready';
  else if (!stoneScanReady) snapshotCompletenessReason = 'stone_scan_not_ready';
  else if (!scanCompleted) snapshotCompletenessReason = 'scan_not_completed';
  else if (unresolvedInventoryItems) snapshotCompletenessReason = 'inventory_items_unresolved';
  else if (rejectBlankInventory) snapshotCompletenessReason = 'blank_payload_rejected';
  else if (quarantineBlankInventory) snapshotCompletenessReason = 'blank_payload_quarantined';
  else if (partialInfo?.isPartial) snapshotCompletenessReason = partialInfo.partialSnapshotReason || 'partial_snapshot';
  else if (provenEmptyInventory) snapshotCompletenessReason = 'verified_empty_inventory';
  else if (snapshotComplete) snapshotCompletenessReason = 'full_snapshot_verified';

  if (snapshotComplete) {
    baseFields.firstFullSnapshotAt = existing?.firstFullSnapshotAt || now;
    baseFields.lastFullSnapshotAt = now;
  }

  return {
    ...proof,
    ...baseFields,
    payloadType,
    hasLeaderstatsSnapshot,
    hasFishSnapshot,
    hasStoneSnapshot,
    snapshotComplete,
    snapshotCompletenessReason,
    blankPayloadRejected: rejectBlankInventory,
    quarantineBlankInventory,
    preserveExistingInventory,
    provenEmptyInventory,
    inventoryReady: snapshotComplete,
    rejectBlankInventory,
    leaderstatsPreserved: leaderstats.preserved === true,
  };
}

function applyCompletenessFields(session, evaluation, now) {
  if (!session || !evaluation) return session || {};
  return {
    ...session,
    firstSeenAt: evaluation.firstSeenAt || session.firstSeenAt || now,
    firstFullSnapshotAt: evaluation.firstFullSnapshotAt || session.firstFullSnapshotAt || null,
    lastFullSnapshotAt: evaluation.lastFullSnapshotAt || session.lastFullSnapshotAt || null,
    hasLeaderstatsSnapshot: evaluation.hasLeaderstatsSnapshot === true,
    hasFishSnapshot: evaluation.hasFishSnapshot === true,
    hasStoneSnapshot: evaluation.hasStoneSnapshot === true,
    snapshotComplete: evaluation.snapshotComplete === true,
    snapshotCompletenessReason: evaluation.snapshotCompletenessReason || null,
    blankPayloadRejected: evaluation.blankPayloadRejected === true,
    provenEmptyInventory: evaluation.provenEmptyInventory === true,
    inventoryReady: evaluation.inventoryReady === true,
    payloadType: evaluation.payloadType || session.payloadType || null,
    firstExecution: evaluation.firstExecution === true,
    replionReady: evaluation.replionReady === true,
    leaderstatsReady: evaluation.leaderstatsReady === true,
    fishScanReady: evaluation.fishScanReady === true,
    stoneScanReady: evaluation.stoneScanReady === true,
    scanCompleted: evaluation.scanCompleted === true,
    scanError: evaluation.scanError || null,
    fishItemCount: evaluation.fishItemCount != null ? evaluation.fishItemCount : session.fishItemCount,
    stoneItemCount: evaluation.stoneItemCount != null ? evaluation.stoneItemCount : session.stoneItemCount,
    playerDataInventoryCount: evaluation.playerDataInventoryCount != null
      ? evaluation.playerDataInventoryCount
      : session.playerDataInventoryCount,
  };
}

function preserveInventoryFields(existing, patch) {
  if (!existing) return patch;
  const out = { ...patch };
  if (existing.items?.length && (!out.items || !out.items.length)) out.items = existing.items;
  if (existing.rawItems?.length && (!out.rawItems || !out.rawItems.length)) out.rawItems = existing.rawItems;
  if (existing.inventory && !out.inventory) out.inventory = existing.inventory;
  if (existing.playerDataFishItems?.length && (!out.playerDataFishItems || !out.playerDataFishItems.length)) {
    out.playerDataFishItems = existing.playerDataFishItems;
  }
  if (existing.playerDataStoneItems?.length && (!out.playerDataStoneItems || !out.playerDataStoneItems.length)) {
    out.playerDataStoneItems = existing.playerDataStoneItems;
  }
  if (existing.playerStats && playerStatsStore.isTrustedPlayerStats(existing.playerStats)) {
    if (!out.playerStats || !playerStatsStore.isTrustedPlayerStats(out.playerStats)) {
      out.playerStats = existing.playerStats;
      out.playerStatsUpdatedAt = existing.playerStatsUpdatedAt || out.playerStatsUpdatedAt;
      out.lastStatsUploadAt = existing.lastStatsUploadAt || out.lastStatsUploadAt;
    }
  }
  out.lastGoodFishItems = existing.lastGoodFishItems || out.lastGoodFishItems;
  out.lastGoodRawItems = existing.lastGoodRawItems || out.lastGoodRawItems;
  out.lastGoodInventory = existing.lastGoodInventory || out.lastGoodInventory;
  out.lastGoodPublicFishCount = existing.lastGoodPublicFishCount || out.lastGoodPublicFishCount;
  return out;
}

function applyHeartbeatUpdate(session, body, now) {
  const evaluation = evaluateSnapshotCompleteness({
    body,
    existing: session,
    isHeartbeat: true,
    now,
  });
  return applyCompletenessFields({
    ...(session || {}),
    lastHeartbeatAt: now,
    lastSuccessfulHeartbeatAt: now,
    payloadType: 'heartbeat',
  }, evaluation, now);
}

function buildSnapshotCompletenessProof(session) {
  if (!session) return null;
  return {
    firstSeenAt: session.firstSeenAt || null,
    firstFullSnapshotAt: session.firstFullSnapshotAt || null,
    lastHeartbeatAt: session.lastHeartbeatAt || null,
    lastSuccessfulUploadAt: session.lastSuccessfulUploadAt || null,
    lastFullSnapshotAt: session.lastFullSnapshotAt || null,
    hasLeaderstatsSnapshot: session.hasLeaderstatsSnapshot === true,
    hasFishSnapshot: session.hasFishSnapshot === true,
    hasStoneSnapshot: session.hasStoneSnapshot === true,
    snapshotComplete: session.snapshotComplete === true,
    inventoryReady: session.inventoryReady === true,
    snapshotCompletenessReason: session.snapshotCompletenessReason || null,
    blankPayloadRejected: session.blankPayloadRejected === true,
    provenEmptyInventory: session.provenEmptyInventory === true,
    payloadType: session.payloadType || null,
    fishItemCount: session.fishItemCount != null ? session.fishItemCount : null,
    stoneItemCount: session.stoneItemCount != null ? session.stoneItemCount : null,
    playerDataInventoryCount: session.playerDataInventoryCount != null
      ? session.playerDataInventoryCount
      : null,
  };
}

module.exports = {
  extractClientSnapshotProof,
  evaluateSnapshotCompleteness,
  applyCompletenessFields,
  preserveInventoryFields,
  applyHeartbeatUpdate,
  buildSnapshotCompletenessProof,
};
