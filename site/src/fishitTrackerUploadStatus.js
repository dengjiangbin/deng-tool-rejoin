'use strict';

const DEFAULT_UPLOAD_INTERVAL_SECONDS = 60;

/** HTTP/proxy failures that must not flip account presence red while last success is fresh. */
const TRANSIENT_UPLOAD_FAILURE_PREFIXES = [
  'server_502',
  'code=502',
  'code=503',
  'code=504',
  'http_502',
  'http_503',
  'http_504',
  'gateway',
  'bad gateway',
  'cloudflare',
  'ingest_unavailable',
  'upstream',
  'proxy',
  'throttle',
  '429',
];

function normalizeTransientUploadFailureReason(reason, statusCode) {
  const code = Number(statusCode);
  if (Number.isFinite(code) && code >= 500 && code < 600) {
    return `server_${code}_upload_retrying`;
  }
  const raw = String(reason || '').trim().toLowerCase();
  if (!raw) return 'server_502_upload_retrying';
  if (raw.startsWith('server_') && raw.includes('_upload_retrying')) return raw;
  const codeMatch = raw.match(/code=(\d{3})/);
  if (codeMatch && Number(codeMatch[1]) >= 500) {
    return `server_${codeMatch[1]}_upload_retrying`;
  }
  if (raw.includes('502')) return 'server_502_upload_retrying';
  if (raw.includes('503')) return 'server_503_upload_retrying';
  if (raw.includes('504')) return 'server_504_upload_retrying';
  return 'server_502_upload_retrying';
}

function isTransientServerUploadFailure(reason, statusCode) {
  const code = Number(statusCode);
  if (Number.isFinite(code) && code >= 500 && code < 600) return true;
  const raw = String(reason || '').trim().toLowerCase();
  if (!raw) return false;
  if (raw.startsWith('server_') && raw.includes('_upload_retrying')) return true;
  return TRANSIENT_UPLOAD_FAILURE_PREFIXES.some((needle) => raw.includes(needle));
}

function uploadStatusThresholds(intervalSeconds) {
  const interval = Math.max(
    1,
    Number(intervalSeconds) > 0 ? Number(intervalSeconds) : DEFAULT_UPLOAD_INTERVAL_SECONDS,
  );
  const onlineFactor = interval <= 15 ? 3 : 2.5;
  const offlineFactor = interval <= 15 ? 6 : 5;
  return {
    uploadIntervalSeconds: interval,
    onlineThresholdSeconds: Math.ceil(interval * onlineFactor),
    offlineThresholdSeconds: Math.ceil(interval * offlineFactor),
  };
}

function resolveIntervalSeconds(data) {
  if (Number(data?.uploadIntervalSeconds) > 0) return Number(data.uploadIntervalSeconds);
  if (Number(data?.intervalSeconds) > 0) return Number(data.intervalSeconds);
  return DEFAULT_UPLOAD_INTERVAL_SECONDS;
}

function resolveHeartbeatTimestamp(data) {
  return data?.lastSuccessfulHeartbeatAt || data?.lastHeartbeatAt || null;
}

function maxIsoTimestamp(candidates) {
  let best = null;
  let bestMs = -1;
  for (const ts of candidates) {
    if (!ts) continue;
    const ms = new Date(ts).getTime();
    if (Number.isFinite(ms) && ms > bestMs) {
      bestMs = ms;
      best = ts;
    }
  }
  return best;
}

function resolveFreshnessTimestamp(data) {
  return maxIsoTimestamp([
    data?.lastSuccessfulUploadAt,
    data?.lastSuccessfulHeartbeatAt,
    data?.lastHeartbeatAt,
    data?.lastStatsUploadAt,
    data?.lastSnapshotUploadAt,
    data?.lastInventoryAt,
    data?.lastStatusAt,
    data?.lastUploadAcceptedAt,
  ]);
}

function countRows(rows) {
  return Array.isArray(rows) ? rows.length : 0;
}

function evaluateAcceptedSnapshotSync(ctx = {}) {
  const {
    completenessEval,
    acceptedCount,
    body,
    playerDataFishItems,
    playerDataStoneItems,
    playerDataTotemItems,
    nextPlayerStatsFields,
    uploadRejected,
    now,
  } = ctx;

  const incomingFish = countRows(body?.fishItems);
  const incomingStone = countRows(body?.stoneItems);
  const incomingTotem = countRows(body?.totemItems);
  const parsedFish = countRows(playerDataFishItems);
  const parsedStone = countRows(playerDataStoneItems);
  const parsedTotem = countRows(playerDataTotemItems);
  const hasInventoryContent = completenessEval?.snapshotComplete === true
    || Number(acceptedCount) > 0
    || incomingFish > 0
    || incomingStone > 0
    || incomingTotem > 0
    || (parsedFish > 0 && !completenessEval?.preserveExistingInventory)
    || (parsedStone > 0 && !completenessEval?.preserveExistingInventory)
    || (parsedTotem > 0 && !completenessEval?.preserveExistingInventory);

  if (uploadRejected) return { accepted: false, reason: 'rejected', hasInventory: false };
  if (completenessEval?.rejectBlankInventory || completenessEval?.blankPayloadRejected) {
    return { accepted: false, reason: 'blank_rejected', hasInventory: false };
  }
  if (completenessEval?.inventoryReady) {
    return {
      accepted: true,
      reason: completenessEval.snapshotComplete ? 'full_snapshot' : 'inventory_snapshot_ready',
      hasInventory: true,
    };
  }
  if (Number(acceptedCount) > 0) {
    return { accepted: true, reason: 'inventory_items', hasInventory: true };
  }

  if (incomingFish > 0 || (parsedFish > 0 && !completenessEval?.preserveExistingInventory)) {
    return { accepted: true, reason: 'fish_snapshot', hasInventory: true };
  }
  if (incomingStone > 0 || (parsedStone > 0 && !completenessEval?.preserveExistingInventory)) {
    return { accepted: true, reason: 'stone_snapshot', hasInventory: true };
  }
  if (incomingTotem > 0 || (parsedTotem > 0 && !completenessEval?.preserveExistingInventory)) {
    return { accepted: true, reason: 'totem_snapshot', hasInventory: true };
  }
  if (nextPlayerStatsFields?.lastStatsUploadAt === now && nextPlayerStatsFields?.playerStats) {
    return { accepted: true, reason: 'leaderstats_snapshot', hasInventory: hasInventoryContent };
  }
  if (completenessEval?.hasLeaderstatsSnapshot && nextPlayerStatsFields?.lastStatsUploadAt === now) {
    return { accepted: true, reason: 'leaderstats_snapshot', hasInventory: hasInventoryContent };
  }

  return { accepted: false, reason: 'no_accepted_content', hasInventory: false };
}

function markTrackerSyncSuccess(session, serverReceivedAt, snapshot = {}) {
  const now = serverReceivedAt || new Date().toISOString();
  const intervalSeconds = Number(snapshot.intervalSeconds) > 0
    ? Number(snapshot.intervalSeconds)
    : resolveIntervalSeconds(session);
  const wasGreen = session?.lastStatus === 'green';
  return {
    ...(session || {}),
    lastStatus: 'green',
    lastStatusAt: now,
    lastSuccessfulUploadAt: now,
    redSince: null,
    inventoryRedSince: null,
    statsRedSince: null,
    lastSyncReason: snapshot.syncReason || 'accepted_snapshot',
    lastUploadAttemptAt: now,
    lastStatusChangeAt: wasGreen ? (session?.lastStatusChangeAt || now) : now,
    lastFailureReason: null,
    lastUploadFailedAt: null,
    lastStatsUpdatedAt: snapshot.lastStatsUpdatedAt || session?.lastStatsUpdatedAt || now,
    lastInventoryAt: snapshot.lastInventoryAt || now,
    lastSnapshotUploadAt: snapshot.lastSnapshotUploadAt || now,
    intervalSeconds,
    graceSeconds: Number(snapshot.graceSeconds) >= 0
      ? Number(snapshot.graceSeconds)
      : (Number(session?.graceSeconds) >= 0 ? Number(session.graceSeconds) : undefined),
    lastPayloadHash: snapshot.payloadHash || session?.lastPayloadHash || null,
    expectedLoaderBuild: snapshot.expectedLoaderBuild || session?.expectedLoaderBuild || null,
    loaderOutdated: snapshot.loaderOutdated === true,
  };
}

function markTrackerSyncMissed(session, checkedAt) {
  const now = checkedAt || new Date().toISOString();
  const wasGreen = session?.lastStatus === 'green';
  return {
    ...(session || {}),
    lastStatus: 'red',
    lastStatusAt: now,
    redSince: session?.redSince || now,
    lastSyncReason: 'upload_interval_missed',
    lastUploadFailedAt: session?.lastUploadFailedAt || now,
    lastStatusChangeAt: wasGreen ? now : (session?.lastStatusChangeAt || now),
  };
}

function resolveInventoryDisplayState(data) {
  if (!data) return 'waiting';
  if (data.provenEmptyInventory === true) return 'empty';
  if (data.snapshotComplete === true || data.inventoryReady === true) return 'ready';
  if (data.lastSuccessfulHeartbeatAt || data.lastHeartbeatAt) return 'syncing';
  return 'waiting';
}

function deriveTrackerUploadAccountStatus(data, opts = {}) {
  const serverNowMs = opts.serverNowMs != null ? opts.serverNowMs : Date.now();
  const serverNow = new Date(serverNowMs).toISOString();
  const expectedTrackerBuild = opts.expectedTrackerBuild || null;
  const isTrustedBuild = typeof opts.isTrustedBuild === 'function' ? opts.isTrustedBuild : null;

  const intervalSeconds = resolveIntervalSeconds(data);
  const thresholds = uploadStatusThresholds(intervalSeconds);

  const freshnessTimestamp = resolveFreshnessTimestamp(data);
  const lastSuccessfulHeartbeatAt = resolveHeartbeatTimestamp(data);
  const lastFailedUploadAt = data?.lastFailedUploadAt || data?.lastUploadFailedAt || null;
  const trackerBuild = data?.trackerBuild || data?.lastUploadTrackerBuild || null;
  const loaderBuild = data?.loaderBuild || trackerBuild || null;
  const snapshotComplete = data?.snapshotComplete === true;
  const inventoryReady = data?.inventoryReady === true || snapshotComplete;
  const lastStatus = data?.lastStatus || null;
  const lastStatusAt = data?.lastStatusAt || null;
  const redSince = data?.redSince || null;
  const latestPayloadAccepted = data?.latestPayloadAccepted !== false
    && !!(freshnessTimestamp || data?.lastUploadAcceptedAt);

  let secondsSinceLastSuccess = null;
  if (freshnessTimestamp) {
    const ageMs = serverNowMs - new Date(freshnessTimestamp).getTime();
    secondsSinceLastSuccess = Number.isFinite(ageMs) && ageMs >= 0
      ? Math.floor(ageMs / 1000)
      : null;
  }

  const buildMismatch = !!(expectedTrackerBuild && trackerBuild && trackerBuild !== expectedTrackerBuild);
  const buildUntrusted = !!(trackerBuild && isTrustedBuild && !isTrustedBuild(trackerBuild));
  const isCurrentBuild = !buildMismatch && !buildUntrusted;

  const neverUploaded = !freshnessTimestamp && !lastStatus && !data?.lastUploadAcceptedAt;

  let status = 'offline';
  let statusColor = 'red';
  let statusDecisionReason = 'no_successful_upload';

  if (neverUploaded) {
    status = 'unknown';
    statusColor = 'unknown';
    statusDecisionReason = 'never_uploaded';
  } else if (buildMismatch || buildUntrusted) {
    statusDecisionReason = buildMismatch ? 'outdated_tracker_build' : 'untrusted_tracker_build';
  } else if (!freshnessTimestamp) {
    statusDecisionReason = lastStatus === 'red' ? 'sync_missed' : 'no_successful_upload';
    if (lastStatus === 'red') statusDecisionReason = 'sync_missed';
  } else if (
    secondsSinceLastSuccess != null
    && secondsSinceLastSuccess > thresholds.offlineThresholdSeconds
  ) {
    statusDecisionReason = 'upload_interval_missed';
  } else if (
    secondsSinceLastSuccess != null
    && secondsSinceLastSuccess <= thresholds.offlineThresholdSeconds
  ) {
    const withinOnline = secondsSinceLastSuccess <= thresholds.onlineThresholdSeconds;
    const transientFailure = isTransientServerUploadFailure(
      data?.lastFailureReason || data?.lastUploadRejectReason || data?.rejectReason,
      data?.lastUploadStatusCodeReturned || data?.lastUploadHttpStatus,
    );
    const hasAcceptedSnapshot = latestPayloadAccepted && (
      data?.hasFishSnapshot || data?.hasStoneSnapshot || data?.hasLeaderstatsSnapshot || inventoryReady
    );

    if (lastStatus === 'green') {
      status = withinOnline ? 'online' : 'syncing';
      statusColor = withinOnline ? 'green' : 'yellow';
      statusDecisionReason = transientFailure
        ? (withinOnline ? 'server_502_upload_retrying' : 'last_success_within_grace')
        : (data?.lastSyncReason || (withinOnline
          ? 'fresh_accepted_snapshot'
          : 'accepted_snapshot_late_within_grace'));
    } else if (
      lastStatus === 'red'
      && transientFailure
      && (data?.lastSuccessfulUploadAt || hasAcceptedSnapshot)
    ) {
      status = withinOnline ? 'online' : 'syncing';
      statusColor = withinOnline ? 'green' : 'yellow';
      statusDecisionReason = withinOnline ? 'server_502_upload_retrying' : 'last_success_within_grace';
    } else if (lastStatus === 'red') {
      statusDecisionReason = data?.lastSyncReason || 'sync_missed';
    } else if (withinOnline && hasAcceptedSnapshot) {
      status = 'online';
      statusColor = 'green';
      statusDecisionReason = 'fresh_accepted_snapshot';
    } else if (withinOnline) {
      status = 'syncing';
      statusColor = 'yellow';
      statusDecisionReason = 'fresh_contact_awaiting_snapshot';
    } else {
      status = 'syncing';
      statusColor = 'yellow';
      statusDecisionReason = hasAcceptedSnapshot
        ? 'accepted_snapshot_late_within_grace'
        : 'contact_late_awaiting_snapshot';
    }
  }

  return {
    serverNow,
    username: data?.username || null,
    robloxUserId: data?.userId != null && Number(data.userId) > 0 ? String(data.userId) : null,
    discordOwnerId: data?.discordOwnerId || null,
    status,
    statusColor,
    lastStatus,
    lastStatusAt,
    redSince: statusColor === 'green' ? null : redSince,
    lastSuccessfulUploadAt: freshnessTimestamp,
    lastSuccessfulHeartbeatAt,
    lastFailedUploadAt,
    secondsSinceLastSuccess,
    uploadIntervalSeconds: thresholds.uploadIntervalSeconds,
    onlineThresholdSeconds: thresholds.onlineThresholdSeconds,
    offlineThresholdSeconds: thresholds.offlineThresholdSeconds,
    statusDecisionReason,
    snapshotComplete,
    inventoryReady,
    snapshotCompletenessReason: data?.snapshotCompletenessReason || null,
    hasLeaderstatsSnapshot: data?.hasLeaderstatsSnapshot === true,
    hasFishSnapshot: data?.hasFishSnapshot === true,
    hasStoneSnapshot: data?.hasStoneSnapshot === true,
    firstFullSnapshotAt: data?.firstFullSnapshotAt || null,
    lastFullSnapshotAt: data?.lastFullSnapshotAt || null,
    blankPayloadRejected: data?.blankPayloadRejected === true,
    provenEmptyInventory: data?.provenEmptyInventory === true,
    payloadType: data?.payloadType || null,
    runId: data?.runId || data?.executionSessionId || null,
    uploadSeq: Number.isFinite(Number(data?.uploadSeq))
      ? Number(data.uploadSeq)
      : (Number.isFinite(Number(data?.uploadRequestCount)) ? Number(data.uploadRequestCount) : null),
    trackerBuild,
    loaderBuild,
    serverReceivedAt: data?.lastUploadReceivedAt || data?.updatedAt || null,
    latestPayloadAccepted,
    rejectReason: data?.lastUploadRejectReason || data?.rejectReason || null,
    accepted: latestPayloadAccepted,
    isCurrentBuild,
    isOldBuild: !isCurrentBuild,
  };
}

function resolveSyncContentTimestamp(data) {
  return maxIsoTimestamp([
    data?.lastSuccessfulUploadAt,
    data?.lastStatsUploadAt,
    data?.lastSnapshotUploadAt,
    data?.lastInventoryAt,
    data?.lastStatusAt,
  ]);
}

function resolveLiveSession(liveTrackDB, { robloxUserId, usernameKey } = {}) {
  const uid = robloxUserId != null ? String(robloxUserId).trim() : '';
  if (uid && /^\d+$/.test(uid)) {
    const aliasKey = liveTrackDB[`uid:${uid}`];
    if (typeof aliasKey === 'string' && liveTrackDB[aliasKey]) {
      return { key: aliasKey, session: liveTrackDB[aliasKey] };
    }
  }
  const key = usernameKey ? String(usernameKey).trim().toLowerCase() : '';
  if (key && liveTrackDB[key]) {
    return { key, session: liveTrackDB[key] };
  }
  return { key: null, session: null };
}

function extractUploadMeta(body) {
  const runId = body?.runId || body?.executionSessionId || body?.executionSession || null;
  const uploadSeq = Number.isFinite(Number(body?.uploadSeq)) ? Number(body.uploadSeq) : null;
  const loaderBuild = body?.loaderBuild ? String(body.loaderBuild).slice(0, 240) : null;
  const intervalSeconds = Number(body?.intervalSeconds) > 0
    ? Number(body.intervalSeconds)
    : (Number(body?.syncIntervalSeconds) > 0 ? Number(body.syncIntervalSeconds) : null);
  return {
    runId: runId ? String(runId).slice(0, 120) : null,
    executionSessionId: runId ? String(runId).slice(0, 120) : null,
    uploadSeq,
    loaderBuild,
    uploadIntervalSeconds: intervalSeconds,
    intervalSeconds,
  };
}

function applyAcceptedUploadMeta(session, body, now, opts = {}) {
  const meta = extractUploadMeta(body);
  const heartbeatOnly = opts.heartbeatOnly === true;
  const online = session?.isOnline !== false;
  const next = {
    ...(session || {}),
    ...meta,
    latestPayloadAccepted: true,
    lastUploadAcceptedAt: now,
    lastUploadReceivedAt: now,
    lastUploadRejectedAt: null,
    lastUploadRejectReason: null,
    rejectReason: null,
    lastAccountSeenAt: now,
    lastSeenAt: now,
  };
  if (heartbeatOnly || online) {
    next.lastHeartbeatAt = now;
    next.lastSuccessfulHeartbeatAt = now;
  }
  if (heartbeatOnly || !session?.lastSuccessfulUploadAt) {
    next.lastSuccessfulUploadAt = session?.lastSuccessfulUploadAt || now;
  }
  return next;
}

function markTrackerHeartbeatSuccess(session, serverReceivedAt, snapshot = {}) {
  const now = serverReceivedAt || new Date().toISOString();
  const intervalSeconds = Number(snapshot.intervalSeconds) > 0
    ? Number(snapshot.intervalSeconds)
    : resolveIntervalSeconds(session);
  return markTrackerSyncSuccess(session, now, {
    syncReason: snapshot.syncReason || 'heartbeat_accepted',
    lastStatsUpdatedAt: session?.lastStatsUpdatedAt || now,
    intervalSeconds,
    expectedLoaderBuild: snapshot.expectedLoaderBuild || session?.expectedLoaderBuild || null,
    loaderOutdated: snapshot.loaderOutdated === true,
    lastInventoryAt: session?.lastInventoryAt || null,
    lastSnapshotUploadAt: session?.lastSnapshotUploadAt || null,
  });
}

function applyRejectedUploadMeta(session, body, now, rejectReason) {
  const meta = extractUploadMeta(body);
  return {
    ...(session || {}),
    ...meta,
    latestPayloadAccepted: false,
    lastFailedUploadAt: now,
    lastUploadFailedAt: now,
    lastUploadRejectedAt: now,
    lastUploadRejectReason: String(rejectReason || 'rejected').slice(0, 240),
    rejectReason: String(rejectReason || 'rejected').slice(0, 240),
  };
}

function applyTransientUploadFailure(session, now, reason, statusCode) {
  const normalized = normalizeTransientUploadFailureReason(reason, statusCode);
  const base = session || {};
  return {
    ...base,
    lastUploadAttemptAt: now,
    lastFailedUploadAt: now,
    lastUploadFailedAt: now,
    lastFailureReason: normalized,
    lastUploadRejectReason: normalized,
    rejectReason: normalized,
    lastUploadFailureIsTransient: true,
    lastUploadStatusCodeReturned: Number.isFinite(Number(statusCode)) ? Number(statusCode) : base.lastUploadStatusCodeReturned,
    // Preserve lastStatus / lastSuccessfulUploadAt — transient proxy errors are not account offline.
    lastSyncReason: base.lastStatus === 'green'
      ? (base.lastSyncReason || 'last_success_within_grace')
      : base.lastSyncReason,
  };
}

module.exports = {
  DEFAULT_UPLOAD_INTERVAL_SECONDS,
  TRANSIENT_UPLOAD_FAILURE_PREFIXES,
  normalizeTransientUploadFailureReason,
  isTransientServerUploadFailure,
  uploadStatusThresholds,
  resolveIntervalSeconds,
  resolveHeartbeatTimestamp,
  resolveFreshnessTimestamp,
  resolveSyncContentTimestamp,
  evaluateAcceptedSnapshotSync,
  markTrackerSyncSuccess,
  markTrackerSyncMissed,
  deriveTrackerUploadAccountStatus,
  resolveInventoryDisplayState,
  resolveLiveSession,
  extractUploadMeta,
  applyAcceptedUploadMeta,
  applyRejectedUploadMeta,
  applyTransientUploadFailure,
  markTrackerHeartbeatSuccess,
};
