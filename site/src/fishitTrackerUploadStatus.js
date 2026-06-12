'use strict';

const DEFAULT_UPLOAD_INTERVAL_SECONDS = 10;

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

function deriveTrackerUploadAccountStatus(data, opts = {}) {
  const serverNowMs = opts.serverNowMs != null ? opts.serverNowMs : Date.now();
  const serverNow = new Date(serverNowMs).toISOString();
  const expectedTrackerBuild = opts.expectedTrackerBuild || null;
  const isTrustedBuild = typeof opts.isTrustedBuild === 'function' ? opts.isTrustedBuild : null;

  const intervalSeconds = resolveIntervalSeconds(data);
  const thresholds = uploadStatusThresholds(intervalSeconds);

  const lastSuccessfulUploadAt = data?.lastSuccessfulUploadAt || null;
  const lastSuccessfulHeartbeatAt = resolveHeartbeatTimestamp(data);
  const freshnessTimestamp = lastSuccessfulHeartbeatAt || lastSuccessfulUploadAt;
  const lastFailedUploadAt = data?.lastFailedUploadAt || data?.lastUploadFailedAt || null;
  const trackerBuild = data?.trackerBuild || data?.lastUploadTrackerBuild || null;
  const loaderBuild = data?.loaderBuild || trackerBuild || null;
  const snapshotComplete = data?.snapshotComplete === true;
  const inventoryReady = data?.inventoryReady === true || snapshotComplete;
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

  let status = 'offline';
  let statusColor = 'red';
  let statusDecisionReason = 'no_successful_heartbeat';

  if (buildMismatch || buildUntrusted) {
    statusDecisionReason = buildMismatch ? 'outdated_tracker_build' : 'untrusted_tracker_build';
  } else if (!freshnessTimestamp) {
    statusDecisionReason = 'no_successful_heartbeat';
  } else if (
    secondsSinceLastSuccess != null
    && secondsSinceLastSuccess <= thresholds.offlineThresholdSeconds
  ) {
    if (
      secondsSinceLastSuccess != null
      && secondsSinceLastSuccess <= thresholds.onlineThresholdSeconds
    ) {
      if (inventoryReady) {
        status = 'online';
        statusColor = 'green';
        statusDecisionReason = 'fresh_heartbeat_full_snapshot_ready';
      } else {
        status = 'syncing';
        statusColor = 'yellow';
        statusDecisionReason = 'fresh_heartbeat_awaiting_full_snapshot';
      }
    } else {
      status = 'syncing';
      statusColor = 'yellow';
      statusDecisionReason = inventoryReady
        ? 'heartbeat_late_within_grace'
        : 'heartbeat_late_awaiting_full_snapshot';
    }
  } else {
    statusDecisionReason = 'no_fresh_heartbeat_after_grace';
  }

  return {
    serverNow,
    username: data?.username || null,
    robloxUserId: data?.userId != null && Number(data.userId) > 0 ? String(data.userId) : null,
    discordOwnerId: data?.discordOwnerId || null,
    status,
    statusColor,
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

function applyAcceptedUploadMeta(session, body, now) {
  const meta = extractUploadMeta(body);
  return {
    ...(session || {}),
    ...meta,
    latestPayloadAccepted: true,
    lastUploadAcceptedAt: now,
    lastUploadRejectedAt: null,
    lastUploadRejectReason: null,
    rejectReason: null,
  };
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

module.exports = {
  DEFAULT_UPLOAD_INTERVAL_SECONDS,
  uploadStatusThresholds,
  resolveIntervalSeconds,
  resolveHeartbeatTimestamp,
  deriveTrackerUploadAccountStatus,
  resolveLiveSession,
  extractUploadMeta,
  applyAcceptedUploadMeta,
  applyRejectedUploadMeta,
};
