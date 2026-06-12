'use strict';

/**
 * Live Tracker account row stats — tracker upload / leaderstats only.
 * Separate from dashboard bot DB stats (fishitDb.getOwnerDashboard).
 */

function serializeLiveTrackerAccountStats(data, playerStatsStore, resolvePlayerStatsForApi) {
  const empty = {
    coin: null,
    coins: null,
    coinsText: null,
    totalCaught: null,
    totalCaughtText: null,
    rarestFish: null,
    rarestFishChance: null,
    statsSource: null,
    statsProven: false,
    emptyReason: 'no_session_data',
    lastSuccessfulUploadAt: null,
    latestSnapshotId: null,
    runId: null,
    uploadSeq: null,
  };
  if (!data || typeof data !== 'object') return empty;

  const lastSuccessfulUploadAt = data.lastSuccessfulUploadAt
    || data.lastStatsUploadAt
    || data.playerStatsUpdatedAt
    || data.lastInventoryAt
    || null;
  const runId = data.runId != null ? String(data.runId) : null;
  const uploadSeq = data.uploadSeq != null ? data.uploadSeq : null;

  const normalized = typeof resolvePlayerStatsForApi === 'function'
    ? resolvePlayerStatsForApi(data.playerStats)
    : (typeof playerStatsStore.normalizePlayerStatsForApi === 'function'
      ? playerStatsStore.normalizePlayerStatsForApi(data.playerStats)
      : null);

  if (!normalized) {
    const connected = data.statusColor === 'green' || data.currentStatus === 'green'
      || data.accountOnline === true;
    let emptyReason = 'stats_not_in_latest_upload';
    if (!data.playerStats) emptyReason = 'player_stats_missing_from_session';
    else if (data.playerStats && !playerStatsStore.isTrustedPlayerStats(data.playerStats)) {
      emptyReason = 'player_stats_untrusted_build_or_source';
    } else if (!playerStatsStore.hasPlayerStatValues(data.playerStats)) {
      emptyReason = 'player_stats_empty_in_session';
    }
    return {
      ...empty,
      emptyReason,
      lastSuccessfulUploadAt,
      latestSnapshotId: runId,
      runId,
      uploadSeq,
      statsProven: false,
      connected,
    };
  }

  const statsSource = normalized.source || null;
  const coinsText = normalized.coinsText || null;
  const totalCaughtText = normalized.totalCaughtText || null;
  const rarestFishChance = normalized.rarestFishChance || null;

  return {
    coin: coinsText,
    coins: normalized.coins != null ? normalized.coins : null,
    coinsText,
    totalCaught: normalized.totalCaught != null ? normalized.totalCaught : null,
    totalCaughtText,
    rarestFish: rarestFishChance,
    rarestFishChance,
    statsSource,
    statsProven: true,
    emptyReason: null,
    lastSuccessfulUploadAt,
    latestSnapshotId: runId,
    runId,
    uploadSeq,
    statsAt: normalized.statsAt || data.playerStatsUpdatedAt || null,
    trackerBuild: normalized.build || data.trackerBuild || null,
  };
}

module.exports = {
  serializeLiveTrackerAccountStats,
};
