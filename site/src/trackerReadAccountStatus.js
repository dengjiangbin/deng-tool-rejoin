'use strict';

/**
 * Fast account-status for deng-tracker-read (8793) — RAM + precompute only.
 * Avoids proxying to 8791 which reloads sharded session files per poll.
 */

const inventoryTrackedAccounts = require('./inventoryTrackedAccounts');
const { ACCOUNT_ONLINE_THRESHOLD_MS } = require('./trackerAccountPresence');
const { BUILD_MARKER } = require('./trackerAccountSummary');

function normalizeTrackedAccount(acct) {
  const usernameKey = acct.robloxUsernameKey
    || acct.roblox_username_key
    || String(acct.robloxUsername || acct.roblox_username || acct.displayName || acct.display_name || '')
      .trim()
      .toLowerCase();
  const robloxUserId = acct.robloxUserId || acct.roblox_user_id || null;
  const username = acct.robloxUsername || acct.roblox_username || acct.displayName || acct.display_name || usernameKey;
  return { usernameKey, robloxUserId, username };
}

function parseBody(hit) {
  if (!hit || !hit.json) return {};
  try {
    const parsed = JSON.parse(hit.json);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

function buildAccountRow(acct, hit, contract, serverNowMs, discordOwnerId) {
  const norm = normalizeTrackedAccount(acct);
  const body = parseBody(hit);
  const liveAccountStats = body.liveAccountStats && typeof body.liveAccountStats === 'object'
    ? body.liveAccountStats
    : {};
  const isOnline = contract.isOnline === true;
  const username = norm.username || body.username || norm.usernameKey;
  const robloxUserId = norm.robloxUserId ? String(norm.robloxUserId) : (body.userId ? String(body.userId) : null);

  return {
    username,
    robloxUserId,
    canonicalKey: robloxUserId || norm.usernameKey,
    discordOwnerId,
    accountPresenceLive: isOnline,
    accountOnline: isOnline,
    accountPresenceStatus: contract.presenceState || (isOnline ? 'online' : 'offline'),
    accountPresenceReason: contract.accountPresenceReason || null,
    accountPresenceGraceSeconds: contract.accountPresenceGraceSeconds || null,
    uploadWarningReason: contract.uploadWarningReason || null,
    statusColor: isOnline ? 'green' : 'red',
    lastRealRobloxStatusAt: contract.lastRealStatusAt || body.lastRealRobloxStatusAt || null,
    serverReceivedStatusAt: contract.serverReceivedStatusAt || body.serverReceivedStatusAt || null,
    statusRevision: contract.statusRevision != null ? contract.statusRevision : null,
    statusReportId: contract.statusReportId || null,
    statusSeq: contract.statusSeq != null ? contract.statusSeq : null,
    sessionId: contract.sessionId || null,
    statusAgeSeconds: contract.statusAgeSeconds != null ? contract.statusAgeSeconds : null,
    statusDecisionReason: contract.statusDecisionReason || null,
    missedStatusReports: contract.missedStatusReports != null ? contract.missedStatusReports : null,
    isStatusStale: contract.isStatusStale === true,
    leaderstatsRevision: contract.leaderstatsRevision != null ? contract.leaderstatsRevision : null,
    lastRealLeaderstatsAt: contract.lastRealLeaderstatsAt || null,
    inventoryRevision: contract.inventoryRevision != null ? contract.inventoryRevision : null,
    lastRealInventoryAt: contract.lastRealInventoryAt || null,
    preservedDataReason: !isOnline && contract.hasRenderableData ? 'offline_preserve_last_known' : null,
    inventoryUploadFresh: contract.inventoryAgeSeconds != null && contract.inventoryAgeSeconds <= 120,
    inventoryUploadStatus: body.inventoryUploadStatus || null,
    lastSnapshotUploadAt: body.lastSnapshotUploadAt || body.lastInventoryAt || null,
    statsUploadFresh: contract.leaderstatsAgeSeconds != null && contract.leaderstatsAgeSeconds <= 120,
    statsUploadStatus: body.statsUploadStatus || null,
    lastStatsUploadAt: body.lastStatsUploadAt || body.leaderstatsUploadedAt || null,
    statusLastSuccessAt: contract.lastRealStatusAt || body.lastSuccessfulHeartbeatAt || null,
    leaderstatsLastSuccessAt: contract.lastRealLeaderstatsAt || null,
    inventoryLastSuccessAt: contract.lastRealInventoryAt || null,
    secondsSinceLastStatusSuccess: contract.statusAgeSeconds,
    secondsSinceLastLeaderstatsSuccess: contract.leaderstatsAgeSeconds,
    secondsSinceLastInventorySuccess: contract.inventoryAgeSeconds,
    uploadIntervalSeconds: Number(body.intervalSeconds) > 0 ? Number(body.intervalSeconds) : 60,
    inventoryDisplayState: body.inventoryDisplayState || (contract.hasRenderableData ? 'ready' : 'waiting'),
    statsProven: liveAccountStats.statsProven === true || body.statsProven === true,
    playerStatsProven: liveAccountStats.statsProven === true || body.playerStatsProven === true,
    coin: liveAccountStats.coin ?? body.coin ?? null,
    coins: liveAccountStats.coins ?? body.coins ?? null,
    coinsText: liveAccountStats.coinsText ?? body.coinsText ?? null,
    totalCaught: liveAccountStats.totalCaught ?? body.totalCaught ?? null,
    totalCaughtText: liveAccountStats.totalCaughtText ?? body.totalCaughtText ?? null,
    rarestFish: liveAccountStats.rarestFish ?? body.rarestFish ?? null,
    rarestFishChance: liveAccountStats.rarestFishChance ?? body.rarestFishChance ?? null,
    statsSource: liveAccountStats.statsSource ?? body.statsSource ?? null,
    emptyReason: liveAccountStats.emptyReason ?? body.emptyReason ?? null,
    statsAt: liveAccountStats.statsAt ?? body.statsAt ?? null,
    trackerBuild: body.trackerBuild || liveAccountStats.trackerBuild || null,
    lastSuccessfulUploadAt: body.lastSuccessfulUploadAt || body.lastSuccessfulHeartbeatAt || null,
    serverReceivedAt: body.serverReceivedAt || body.lastUploadReceivedAt || null,
    liveAccountStats,
    snapshotSource: 'precomputed_read',
  };
}

function buildOfflineRow(acct, discordOwnerId) {
  const norm = normalizeTrackedAccount(acct);
  const robloxUserId = norm.robloxUserId ? String(norm.robloxUserId) : null;
  return {
    username: norm.username,
    robloxUserId,
    canonicalKey: robloxUserId || norm.usernameKey,
    discordOwnerId,
    accountPresenceLive: false,
    accountOnline: false,
    accountPresenceStatus: 'offline',
    statusColor: 'red',
    snapshotSource: 'precomputed_miss',
  };
}

async function buildReadAccountStatusPayload(discordOwnerId, deps) {
  const {
    lookupCached,
    hydrateCacheMiss,
    buildPresenceContract,
  } = deps;
  const serverNowMs = Date.now();
  const serverNow = new Date(serverNowMs).toISOString();
  const trackedAccounts = await inventoryTrackedAccounts.listTrackedAccounts(discordOwnerId);
  const list = Array.isArray(trackedAccounts) ? trackedAccounts : [];
  let onlineCount = 0;
  const accounts = list.map((acct) => {
    const norm = normalizeTrackedAccount(acct);
    const key = norm.usernameKey;
    if (!key) return buildOfflineRow(acct, discordOwnerId);
    let hit = lookupCached(key);
    if (!hit) hit = hydrateCacheMiss(key);
    if (!hit) return buildOfflineRow(acct, discordOwnerId);
    const contract = buildPresenceContract(hit, serverNowMs);
    if (contract.isOnline) onlineCount += 1;
    return buildAccountRow(acct, hit, contract, serverNowMs, discordOwnerId);
  });
  const trackedCount = list.length;
  return {
    ok: true,
    buildMarker: BUILD_MARKER,
    trackedCount,
    onlineCount,
    offlineCount: Math.max(0, trackedCount - onlineCount),
    accounts,
    generatedAt: serverNow,
    serverNow,
    freshnessWindowMs: ACCOUNT_ONLINE_THRESHOLD_MS,
    sources: {
      trackedList: 'inventory_tracked_accounts',
      heartbeat: 'precompute.presence_json',
      presence: 'trackerReadApp.buildPresenceContract',
      lane: 'deng-tracker-read',
    },
  };
}

module.exports = {
  buildReadAccountStatusPayload,
};
