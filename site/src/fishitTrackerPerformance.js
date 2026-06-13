'use strict';

const DASHBOARD_CACHE_PRESET_MS = Number(process.env.TRACKER_DASHBOARD_CACHE_PRESET_MS) || 60_000;
const DASHBOARD_CACHE_CUSTOM_MS = Number(process.env.TRACKER_DASHBOARD_CACHE_CUSTOM_MS) || 30_000;
/** @deprecated use preset/custom TTL helpers */
const DASHBOARD_CACHE_MS = DASHBOARD_CACHE_PRESET_MS;

const PRESET_DASHBOARD_PERIODS = new Set(['all', '30d', '7d', 'ytd', 'tdy']);

const dashboardCache = new Map();

function dashboardCacheKey(ownerId, period, from, to) {
  return `${ownerId}|${period}|${from || ''}|${to || ''}`;
}

function dashboardCacheTtl(period) {
  return PRESET_DASHBOARD_PERIODS.has(period) ? DASHBOARD_CACHE_PRESET_MS : DASHBOARD_CACHE_CUSTOM_MS;
}

function getCachedDashboard(key, period) {
  const entry = dashboardCache.get(key);
  if (!entry) return null;
  const ttl = dashboardCacheTtl(period || entry.period || 'custom');
  if (Date.now() - entry.at > ttl) {
    dashboardCache.delete(key);
    return null;
  }
  return entry.payload;
}

function setCachedDashboard(key, payload, period) {
  dashboardCache.set(key, { at: Date.now(), payload, period: period || 'custom' });
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
  'items', 'inventory', 'counts', 'fishItems', 'stoneItems', 'totemItems',
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

function slimFishCards(cards) {
  if (!Array.isArray(cards)) return [];
  return cards.map((card) => ({
    name: card.name,
    rarity: card.rarity,
    count: card.count != null ? card.count : card.amount,
    imageUrl: card.imageUrl || null,
    // Carry the Roblox asset id so the frontend can use the same proxy
    // fallback (/api/fishit-tracker/image/<assetId>) the inventory uses.
    imageAssetId: card.imageAssetId || null,
  }));
}

function slimDailyCaught(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => ({
    date: row.date,
    totalCaught: row.totalCaught,
    bucket: row.bucket,
  }));
}

function buildDashboardResponse(payload, includeDebug) {
  const body = stripDashboardDebug(payload, includeDebug);
  const fishCards = includeDebug ? (body.fishCards || []) : slimFishCards(body.fishCards);
  const dailyCaught = includeDebug ? (body.dailyCaught || []) : slimDailyCaught(body.dailyCaught);
  const cards = body.cards || { secretCaught: 0, forgottenCaught: 0 };
  return {
    ok: true,
    period: body.period,
    periodLabel: body.periodLabel,
    from: body.from,
    to: body.to,
    available: body.available,
    statsState: body.statsState,
    emptyReason: body.emptyReason || null,
    cards: {
      secretCaught: cards.secretCaught || 0,
      forgottenCaught: cards.forgottenCaught || 0,
    },
    summary: {
      secretCaught: cards.secretCaught || 0,
      forgottenCaught: cards.forgottenCaught || 0,
    },
    fishCards,
    dailyCaught,
    scope: body.scope,
    discordUserId: body.discordUserId,
    trackedAccountCount: body.trackedAccountCount,
    source: body.source,
    ...(includeDebug && payload.debug ? { debug: payload.debug } : {}),
  };
}

module.exports = {
  DASHBOARD_CACHE_MS,
  DASHBOARD_CACHE_PRESET_MS,
  DASHBOARD_CACHE_CUSTOM_MS,
  PRESET_DASHBOARD_PERIODS,
  dashboardCacheKey,
  dashboardCacheTtl,
  getCachedDashboard,
  setCachedDashboard,
  clearDashboardCache,
  isDebugRequest,
  isLiteBackpackRequest,
  buildLiteBackpackResponse,
  stripDashboardDebug,
  slimFishCards,
  slimDailyCaught,
  buildDashboardResponse,
};
