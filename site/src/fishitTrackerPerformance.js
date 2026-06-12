'use strict';

const DASHBOARD_CACHE_MS = Number(process.env.TRACKER_DASHBOARD_CACHE_MS) || 10000;

const dashboardCache = new Map();

function dashboardCacheKey(ownerId, period, from, to) {
  return `${ownerId}|${period}|${from || ''}|${to || ''}`;
}

function getCachedDashboard(key) {
  const entry = dashboardCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.at > DASHBOARD_CACHE_MS) {
    dashboardCache.delete(key);
    return null;
  }
  return entry.payload;
}

function setCachedDashboard(key, payload) {
  dashboardCache.set(key, { at: Date.now(), payload });
}

function clearDashboardCache() {
  dashboardCache.clear();
}

function isDebugRequest(req) {
  const q = (req && req.query) || {};
  return q.debug === '1' || q.debug === 'true' || q.debug === 'global';
}

function isLiteBackpackRequest(req) {
  const q = (req && req.query) || {};
  if (q.full === '1' || q.full === 'true') return false;
  if (q.lite === '1' || q.lite === 'true') return true;
  if (isDebugRequest(req)) return false;
  if (process.env.NODE_ENV === 'test') return false;
  return true;
}

const LITE_BACKPACK_KEYS = [
  'username', 'userId', 'renderBuild', 'publicApiBuild', 'trackerBuild',
  'inventorySource', 'sourceTruth',
  'items', 'inventory', 'counts', 'fishItems', 'stoneItems',
  'activationState', 'publicItems', 'publicFishItems', 'fishInventory', 'stoneInventory',
  'fishCounts', 'publicCounts',
  'lastInventoryAt', 'lastSeenAt', 'lastHeartbeatAt', 'lastAccountSeenAt',
  'lastSnapshotUploadAt', 'lastStatsUploadAt', 'lastSuccessfulUploadAt',
  'lastLoaderErrorAt', 'lastLoaderErrorMessage',
  'expectedLoaderBuild', 'lastLoaderBuild',
  'isOnline', 'loaderOnline', 'connectionLive',
  'accountPresenceLive', 'accountPresenceStatus', 'accountPresenceReason',
  'accountOnline', 'accountOnlineStatus', 'accountStatusReason',
  'accountPresenceGraceSeconds',
  'inGameStatus', 'currentStatus', 'status', 'statusColor', 'statusDecisionReason',
  'secondsSinceLastSuccess', 'onlineThresholdSeconds', 'offlineThresholdSeconds',
  'uploadIntervalSeconds', 'runId', 'uploadSeq', 'loaderBuild', 'serverReceivedAt',
  'latestPayloadAccepted', 'rejectReason', 'isCurrentBuild', 'isOldBuild',
  'provenEmptyInventory', 'snapshotComplete', 'inventoryReady',
  'snapshotCompletenessReason', 'hasLeaderstatsSnapshot', 'hasFishSnapshot', 'hasStoneSnapshot',
  'firstFullSnapshotAt', 'lastFullSnapshotAt', 'blankPayloadRejected', 'payloadType',
  'inventoryDisplayState',
  'connectionStatus', 'connectionStatusColor', 'connectionStatusReason',
  'uploadSyncFresh', 'uploadSyncStatus', 'uploadSyncRedSince', 'uploadSyncRedDurationSeconds',
  'lastStatsUpdatedAt', 'statsUploadFresh', 'statsUploadStatus', 'statsRedSince',
  'inventoryUploadFresh', 'inventoryUploadStatus', 'inventorySyncStatus', 'inventorySyncReason',
  'lastInventorySyncAt', 'lastFishStoneSyncAt', 'fishStoneSyncStatus',
  'inventoryRedSince', 'inventoryStaleAfterSeconds',
  'statsFresh', 'redSince', 'redDurationSeconds', 'intervalSeconds', 'graceSeconds',
  'lastUploadAttemptAt', 'lastUploadFailedAt', 'lastFailureReason',
  'lastStatusChangeAt', 'lastPayloadHash',
  'dataStale', 'lastGoodFishPreserved', 'lastGoodPublicFishCount',
  'playerStats', 'playerStatsProven', 'playerStatsUpdatedAt',
  'liveAccountStats', 'statsSource', 'statsEmptyReason',
  'updatedAt', 'serverNow',
];

function buildLiteBackpackResponse(full) {
  const out = { lite: true, responseMode: 'lite' };
  for (const key of LITE_BACKPACK_KEYS) {
    if (full[key] !== undefined) out[key] = full[key];
  }
  return out;
}

function stripDashboardDebug(payload, includeDebug) {
  if (!payload || includeDebug) return payload;
  if (!payload.debug) return payload;
  const { debug, ...rest } = payload;
  return rest;
}

function buildDashboardResponse(payload, includeDebug) {
  const body = stripDashboardDebug(payload, includeDebug);
  return {
    ok: true,
    ...body,
    summary: body.cards,
    ...(includeDebug && payload.debug ? { debug: payload.debug } : {}),
  };
}

module.exports = {
  DASHBOARD_CACHE_MS,
  dashboardCacheKey,
  getCachedDashboard,
  setCachedDashboard,
  clearDashboardCache,
  isDebugRequest,
  isLiteBackpackRequest,
  buildLiteBackpackResponse,
  stripDashboardDebug,
  buildDashboardResponse,
};
