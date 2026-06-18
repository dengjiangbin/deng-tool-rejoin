'use strict';

const uploadAccountStatus = require('./fishitTrackerUploadStatus');
const {
  ACCOUNT_ONLINE_THRESHOLD_MS,
  deriveAccountPresenceStatus,
  resolveLastAccountSeenAt,
} = require('./trackerAccountPresence');

const BUILD_MARKER = 'TRACKER_COUNTS_AND_10S_POLL_FIX_2026_06_14';

function normalizeTrackedAccount(acct) {
  const usernameKey = acct.robloxUsernameKey
    || acct.roblox_username_key
    || String(acct.robloxUsername || acct.roblox_username || acct.displayName || acct.display_name || '')
      .trim()
      .toLowerCase();
  const robloxUserId = acct.robloxUserId || acct.roblox_user_id || null;
  const username = acct.robloxUsername || acct.roblox_username || acct.displayName || acct.display_name || usernameKey;
  return { usernameKey, robloxUserId, username, raw: acct };
}

function buildTrackerAccountSummary(trackedAccounts, liveTrackDB, opts = {}) {
  const serverNowMs = opts.serverNowMs != null ? opts.serverNowMs : Date.now();
  const serverNow = new Date(serverNowMs).toISOString();
  const expectedTrackerBuild = opts.expectedTrackerBuild || null;
  const isTrustedBuild = typeof opts.isTrustedBuild === 'function' ? opts.isTrustedBuild : null;
  const discordOwnerId = opts.discordOwnerId || null;
  // Online decision uses the tight authoritative threshold (150s) so a polled
  // account-status summary flips red as soon as real heartbeats stop, identical
  // to the per-account presence used by get-backpack.
  const freshnessWindowMs = opts.freshnessWindowMs || ACCOUNT_ONLINE_THRESHOLD_MS;
  const list = Array.isArray(trackedAccounts) ? trackedAccounts : [];
  const store = liveTrackDB && typeof liveTrackDB === 'object' ? liveTrackDB : {};

  const accounts = [];
  let onlineCount = 0;

  for (const acct of list) {
    const norm = normalizeTrackedAccount(acct);
    const { session } = uploadAccountStatus.resolveLiveSession(store, {
      robloxUserId: norm.robloxUserId,
      usernameKey: norm.usernameKey,
    });
    const sessionData = session
      ? { ...session, discordOwnerId }
      : {
        username: norm.username,
        userId: norm.robloxUserId,
        discordOwnerId,
      };
    const uploadStatus = uploadAccountStatus.deriveTrackerUploadAccountStatus(sessionData, {
      serverNowMs,
      expectedTrackerBuild,
      isTrustedBuild,
    });
    const presence = deriveAccountPresenceStatus(sessionData, freshnessWindowMs, serverNowMs);
    const online = presence.accountPresenceLive === true;
    if (online) onlineCount += 1;
    accounts.push({
      username: uploadStatus.username || norm.username,
      robloxUserId: uploadStatus.robloxUserId || (norm.robloxUserId ? String(norm.robloxUserId) : null),
      canonicalKey: norm.robloxUserId ? String(norm.robloxUserId) : norm.usernameKey,
      accountPresenceLive: presence.accountPresenceLive,
      accountOnline: presence.accountOnline,
      accountPresenceStatus: presence.accountPresenceStatus,
      accountPresenceReason: presence.accountPresenceReason,
      lastAccountSeenAt: resolveLastAccountSeenAt(sessionData),
      lastSuccessfulUploadAt: uploadStatus.lastSuccessfulUploadAt || sessionData.lastSuccessfulUploadAt || null,
      serverReceivedAt: uploadStatus.serverReceivedAt || sessionData.lastUploadReceivedAt || null,
      statusColor: uploadStatus.statusColor,
      secondsSinceLastSuccess: uploadStatus.secondsSinceLastSuccess,
    });
  }

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
    freshnessWindowMs,
    sources: {
      trackedList: 'inventory_tracked_accounts',
      heartbeat: 'liveTrackDB.lastAccountSeenAt|lastSuccessfulUploadAt|lastUploadReceivedAt',
      presence: 'trackerAccountPresence.deriveAccountPresenceStatus',
      notFrom: 'stabilitySnapshot',
    },
  };
}

module.exports = {
  BUILD_MARKER,
  buildTrackerAccountSummary,
};
