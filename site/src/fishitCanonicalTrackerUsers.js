'use strict';

const { EXPECTED_CLIENT_TRACKER_BUILD } = require('./fishitTrackerBuild');

const USERNAME_KEY_RE = /^[a-z0-9_]{3,20}$/;
const ACCOUNT_PRESENCE_GRACE_MS = 120000;

function normaliseUsername(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const key = raw.toLowerCase();
  return USERNAME_KEY_RE.test(key) ? key : '';
}

function normaliseUserId(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.floor(n);
}

function parseTimestampMs(value) {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function syncAgeSecondsFromTimestamp(ts, nowMs = Date.now()) {
  const ms = parseTimestampMs(ts);
  if (ms == null) return null;
  return Math.max(0, Math.floor((nowMs - ms) / 1000));
}

function resolveLastAccountSeenAt(data) {
  if (!data) return null;
  if (data.lastAccountSeenAt) return data.lastAccountSeenAt;
  const candidates = [
    data.lastHeartbeatAt,
    data.lastSeenAt,
    data.lastSnapshotUploadAt,
    data.lastInventoryAt,
  ].filter(Boolean);
  if (!candidates.length) return null;
  let best = candidates[0];
  let bestMs = new Date(best).getTime();
  for (let i = 1; i < candidates.length; i += 1) {
    const ms = new Date(candidates[i]).getTime();
    if (Number.isFinite(ms) && ms > bestMs) {
      best = candidates[i];
      bestMs = ms;
    }
  }
  return best;
}

function isTrustedClientBuild(build, expectedBuild = EXPECTED_CLIENT_TRACKER_BUILD) {
  if (!build) return false;
  const s = String(build);
  return s === expectedBuild || s.includes('LOADER_REGISTER_LIMIT_FIX');
}

function deriveAccountPresenceLive(data, maxAgeMs = ACCOUNT_PRESENCE_GRACE_MS, nowMs = Date.now()) {
  const lastAccountSeenAt = resolveLastAccountSeenAt(data);
  const seenAgeSeconds = syncAgeSecondsFromTimestamp(lastAccountSeenAt, nowMs);
  const loaderBuild = data?.trackerBuild || data?.lastUploadTrackerBuild || null;
  if (!data) return { live: false, reason: 'no_session' };
  if (loaderBuild && !isTrustedClientBuild(loaderBuild)) {
    return { live: false, reason: 'outdated_loader' };
  }
  if (!lastAccountSeenAt) return { live: false, reason: 'no_session' };
  const recentSeen = seenAgeSeconds != null && seenAgeSeconds * 1000 < maxAgeMs;
  const loaderOnline = data.isOnline === true;
  const loaderOffline = data.isOnline === false;
  if (recentSeen) {
    if (loaderOffline) return { live: false, reason: 'client_offline' };
    return { live: true, reason: loaderOnline ? 'heartbeat' : 'loader_contact' };
  }
  if (loaderOffline) return { live: false, reason: 'client_offline' };
  return { live: false, reason: 'stale_heartbeat' };
}

function resolveUniqueKey(data) {
  const userId = normaliseUserId(data?.userId);
  if (userId) return `uid:${userId}`;
  const username = normaliseUsername(data?.username);
  if (username) return `name:${username}`;
  return null;
}

function resolveLatestSuccessfulUploadAt(data) {
  return data?.lastSuccessfulUploadAt
    || data?.lastUploadAcceptedAt
    || data?.lastInventoryAt
    || data?.lastSnapshotUploadAt
    || null;
}

function extractTrackerProof(data) {
  const proof = data?.trackerClientProof && typeof data.trackerClientProof === 'object'
    ? data.trackerClientProof
    : {};
  return {
    trackerBuild: data?.trackerBuild || proof.trackerBuild || null,
    loaderBuild: data?.trackerBuild || data?.lastUploadTrackerBuild || proof.trackerBuild || null,
    replionSourceOfTruth: proof.replionSourceOfTruth === true || data?.replionSourceOfTruth === true,
    phase: String(data?.phase || proof.phase || '').trim().toLowerCase(),
    isOnline: data?.isOnline === true || proof.isOnline === true || proof.online === true,
  };
}

function evaluateCanonicalUser(data, opts = {}) {
  const expectedBuild = opts.expectedBuild || EXPECTED_CLIENT_TRACKER_BUILD;
  const nowMs = opts.nowMs || Date.now();
  const username = normaliseUsername(data?.username);
  const robloxUserId = normaliseUserId(data?.userId);
  const uniqueKey = resolveUniqueKey(data);
  const latestSuccessfulUploadAt = resolveLatestSuccessfulUploadAt(data);
  const proof = extractTrackerProof(data);
  const presence = deriveAccountPresenceLive(data, opts.maxAgeMs || ACCOUNT_PRESENCE_GRACE_MS, nowMs);
  const duplicateUploadCount = Number(data?.duplicateUploadCount) >= 0
    ? Number(data.duplicateUploadCount)
    : Math.max(0, Number(data?.uploadRequestCount || 1) - 1);

  const base = {
    username: data?.username || username || null,
    robloxUserId,
    uniqueKey,
    latestSuccessfulUploadAt,
    secondsSinceLastUpload: syncAgeSecondsFromTimestamp(latestSuccessfulUploadAt, nowMs),
    trackerBuild: proof.trackerBuild,
    loaderBuild: proof.loaderBuild,
    isOnline: data?.isOnline === true,
    duplicateUploadCount,
    accepted: false,
    rejectReason: null,
    onlineFresh: presence.live,
    onlineReason: presence.reason,
    currentBuild: isTrustedClientBuild(proof.trackerBuild, expectedBuild),
  };

  if (!uniqueKey) {
    return { ...base, rejectReason: 'invalid_unique_key' };
  }
  if (!username) {
    return { ...base, rejectReason: 'missing_username' };
  }
  if (!latestSuccessfulUploadAt) {
    return { ...base, rejectReason: 'no_successful_upload' };
  }
  if (!proof.trackerBuild || !isTrustedClientBuild(proof.trackerBuild, expectedBuild)) {
    return { ...base, rejectReason: 'old_build' };
  }
  const phase = proof.phase || String(data?.phase || '').trim().toLowerCase();
  if (phase && phase !== 'live') {
    return { ...base, rejectReason: 'phase_not_live' };
  }
  if (!proof.replionSourceOfTruth) {
    return { ...base, rejectReason: 'invalid_tracker_proof' };
  }
  if (data?.isOnline === false) {
    return { ...base, rejectReason: 'client_offline_flag' };
  }

  base.accepted = true;
  base.rejectReason = presence.live ? null : (presence.reason === 'outdated_loader' ? 'old_build' : 'stale');
  return base;
}

function pickNewerSession(current, candidate) {
  const currentAt = parseTimestampMs(resolveLatestSuccessfulUploadAt(current.data));
  const candidateAt = parseTimestampMs(resolveLatestSuccessfulUploadAt(candidate.data));
  if (candidateAt == null) return current;
  if (currentAt == null) return candidate;
  if (candidateAt > currentAt) return candidate;
  if (candidateAt < currentAt) return current;
  const currentUploads = Number(current.data?.uploadRequestCount) || 1;
  const candidateUploads = Number(candidate.data?.uploadRequestCount) || 1;
  return candidateUploads >= currentUploads ? candidate : current;
}

function computeCanonicalTrackerUsers(liveTrackDB, opts = {}) {
  const store = liveTrackDB && typeof liveTrackDB === 'object' ? liveTrackDB : {};
  const nowMs = opts.nowMs || Date.now();
  const expectedBuild = opts.expectedBuild || EXPECTED_CLIENT_TRACKER_BUILD;
  const merged = new Map();
  let rawUploadRows = 0;
  let rawSessionRows = 0;
  let invalidPayloadIgnored = 0;

  for (const [sessionKey, data] of Object.entries(store)) {
    if (sessionKey.startsWith('uid:')) continue;
    if (!data || typeof data !== 'object') {
      invalidPayloadIgnored += 1;
      continue;
    }
    rawSessionRows += 1;
    rawUploadRows += Math.max(1, Number(data.uploadRequestCount) || 1);
    const uniqueKey = resolveUniqueKey(data);
    if (!uniqueKey) {
      invalidPayloadIgnored += 1;
      continue;
    }
    const uploadCount = Math.max(1, Number(data.uploadRequestCount) || 1);
    const entry = {
      sessionKey,
      data,
      totalUploads: uploadCount,
      duplicateUploadCount: Math.max(0, uploadCount - 1),
      mergedSessionKeys: [sessionKey],
    };
    const existing = merged.get(uniqueKey);
    if (!existing) {
      merged.set(uniqueKey, entry);
      continue;
    }
    const chosen = pickNewerSession(existing, entry);
    const other = chosen === existing ? entry : existing;
    const chosenUploads = chosen.totalUploads || Math.max(1, Number(chosen.data.uploadRequestCount) || 1);
    const otherUploads = other.totalUploads || Math.max(1, Number(other.data.uploadRequestCount) || 1);
    chosen.totalUploads = chosenUploads + otherUploads;
    chosen.duplicateUploadCount = Math.max(0, chosen.totalUploads - 1);
    chosen.mergedSessionKeys = [...new Set([...(existing.mergedSessionKeys || []), ...(entry.mergedSessionKeys || [])])];
    merged.set(uniqueKey, chosen);
  }

  const users = [];
  let currentBuildUniqueUsers = 0;
  let onlineUniqueUsers = 0;
  let oldBuildIgnored = 0;
  let staleIgnored = 0;

  for (const entry of merged.values()) {
    const row = evaluateCanonicalUser({
      ...entry.data,
      duplicateUploadCount: entry.duplicateUploadCount,
    }, { nowMs, expectedBuild });
    users.push({
      ...row,
      sessionKey: entry.sessionKey,
      mergedSessionKeys: entry.mergedSessionKeys,
    });
    if (!row.username || !row.uniqueKey) {
      invalidPayloadIgnored += 1;
      continue;
    }
    if (!row.latestSuccessfulUploadAt) {
      invalidPayloadIgnored += 1;
      continue;
    }
    if (!row.currentBuild) {
      oldBuildIgnored += 1;
      continue;
    }
    if (!row.accepted) {
      if (row.rejectReason === 'old_build' || row.rejectReason === 'outdated_loader') {
        oldBuildIgnored += 1;
      } else if (
        row.rejectReason === 'invalid_tracker_proof'
        || row.rejectReason === 'missing_username'
        || row.rejectReason === 'invalid_unique_key'
        || row.rejectReason === 'no_successful_upload'
        || row.rejectReason === 'phase_not_live'
        || row.rejectReason === 'client_offline_flag'
      ) {
        invalidPayloadIgnored += 1;
      } else {
        invalidPayloadIgnored += 1;
      }
      continue;
    }
    currentBuildUniqueUsers += 1;
    if (row.onlineFresh) {
      onlineUniqueUsers += 1;
    } else if (row.rejectReason === 'stale') {
      staleIgnored += 1;
    }
  }

  users.sort((a, b) => {
    if (a.accepted !== b.accepted) return a.accepted ? -1 : 1;
    if (a.onlineFresh !== b.onlineFresh) return a.onlineFresh ? -1 : 1;
    return String(a.username || '').localeCompare(String(b.username || ''));
  });

  const uniqueKeysSeen = merged.size;
  const duplicatesRemoved = Math.max(0, rawUploadRows - uniqueKeysSeen);

  return {
    available: currentBuildUniqueUsers > 0 || onlineUniqueUsers > 0 || rawUploadRows > 0,
    rawUploadRows,
    rawSessionRows,
    uniqueKeysSeen,
    duplicatesRemoved,
    currentBuildUniqueUsers,
    onlineUniqueUsers,
    oldBuildIgnored,
    invalidPayloadIgnored,
    staleIgnored,
    expectedBuild,
    onlineStaleMs: ACCOUNT_PRESENCE_GRACE_MS,
    updatedAt: new Date(nowMs).toISOString(),
    summary: {
      rawUploadRows,
      rawSessionRows,
      uniqueKeysSeen,
      duplicatesRemoved,
      currentBuildUniqueUsers,
      onlineUniqueUsers,
      oldBuildIgnored,
      invalidPayloadIgnored,
      staleIgnored,
    },
    users,
  };
}

module.exports = {
  ACCOUNT_PRESENCE_GRACE_MS,
  computeCanonicalTrackerUsers,
  deriveAccountPresenceLive,
  evaluateCanonicalUser,
  isTrustedClientBuild,
  resolveUniqueKey,
  resolveLatestSuccessfulUploadAt,
};
