'use strict';

const { EXPECTED_CLIENT_TRACKER_BUILD, isAllowedTrackerBuild } = require('./fishitTrackerBuild');
const { isTransientServerUploadFailure } = require('./fishitTrackerUploadStatus');
const reportIdentity = require('./trackerReportIdentity');

/** Public status grace — stay green unless uploads fail continuously for 10 minutes. */
const ACCOUNT_PRESENCE_GRACE_MS = 600_000;
const STATUS_CONTINUOUS_FAILURE_GRACE_MS = ACCOUNT_PRESENCE_GRACE_MS;

// Authoritative ONLINE threshold. The loader sends a real isOnline heartbeat
// every 60s, so an account is GREEN only if its last real tracker contact is
// within 2.5x the interval (150s). Past that it is RED (offline / stale / no
// data) — there is no long "looks online for 10 minutes" window. This matches
// the upload-status path (deriveTrackerUploadAccountStatus: min(interval*2.5,600)
// = 150s for a 60s interval), so the row dot and the status pill agree. Override
// with TRACKER_ONLINE_THRESHOLD_MS only for tests/tuning.
const ACCOUNT_ONLINE_THRESHOLD_MS = parseInt(
  process.env.TRACKER_ONLINE_THRESHOLD_MS || '150000',
  10,
);

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

function isTrustedClientBuild(build, expectedBuild = EXPECTED_CLIENT_TRACKER_BUILD) {
  if (isAllowedTrackerBuild(build)) return true;
  if (!build) return false;
  const s = String(build);
  return s === expectedBuild || s.includes('LOADER_REGISTER_LIMIT_FIX');
}

function resolveLastAccountSeenAt(data) {
  if (!data) return null;
  if (data.lastAccountSeenAt) return data.lastAccountSeenAt;
  const candidates = [
    data.lastValidStatusAt,
    data.lastSuccessfulUploadAt,
    data.lastSuccessfulHeartbeatAt,
    data.lastHeartbeatAt,
    data.lastUploadReceivedAt,
    data.lastUploadAcceptedAt,
    data.lastSeenAt,
    data.lastSnapshotUploadAt,
    data.lastInventoryAt,
  ].filter(Boolean);
  if (!candidates.length) return null;
  let best = candidates[0];
  let bestMs = parseTimestampMs(best) || 0;
  for (let i = 1; i < candidates.length; i += 1) {
    const ms = parseTimestampMs(candidates[i]);
    if (ms != null && ms > bestMs) {
      best = candidates[i];
      bestMs = ms;
    }
  }
  return best;
}

function deriveAccountPresenceStatus(data, maxAgeMs = ACCOUNT_ONLINE_THRESHOLD_MS, nowMs = Date.now()) {
  const lastAccountSeenAt = resolveLastAccountSeenAt(data);
  const seenAgeSeconds = syncAgeSecondsFromTimestamp(lastAccountSeenAt, nowMs);
  const loaderBuild = data?.trackerBuild || data?.lastUploadTrackerBuild || null;
  const base = {
    lastAccountSeenAt,
    lastHeartbeatAt: data?.lastHeartbeatAt || null,
    heartbeatAgeSeconds: seenAgeSeconds,
    isOnlineFlag: data?.isOnline === true,
    loaderOutdated: !!(loaderBuild && !isTrustedClientBuild(loaderBuild)),
    accountPresenceGraceSeconds: Math.floor(maxAgeMs / 1000),
  };
  if (!data) {
    return {
      ...base,
      accountPresenceLive: false,
      accountOnline: false,
      accountPresenceStatus: 'offline',
      accountPresenceReason: 'no_session',
      accountStatusReason: 'no_session',
    };
  }
  if (base.loaderOutdated) {
    return {
      ...base,
      accountPresenceLive: false,
      accountOnline: false,
      accountPresenceStatus: 'error',
      accountPresenceReason: 'outdated_loader',
      accountStatusReason: 'outdated_loader',
    };
  }
  // ── Identity-gated authoritative path ────────────────────────────────────
  // When a fresh-unique Roblox status report has ever been recorded for this
  // session, online/offline + age come ONLY from lastRealRobloxStatusAt via the
  // binary grace state machine. This is decoupled from inventory/leaderstats
  // uploads, backend precompute time, and read time. Sessions with no real
  // status identity yet (legacy rows pre-deploy) fall through to the legacy
  // lastAccountSeenAt path below and auto-migrate on their next status report.
  if (reportIdentity.hasRealStatusIdentity(data)) {
    const st = reportIdentity.evaluateStatusState(data, nowMs);
    const reasonMap = {
      fresh_status_report: 'heartbeat',
      within_grace_missed_report: 'loader_contact',
      client_offline: 'client_offline',
      hard_offline_timeout: 'account_offline_timeout',
      no_status_report: 'no_session',
    };
    const mappedReason = reasonMap[st.statusDecisionReason] || st.statusDecisionReason;
    return {
      ...base,
      lastAccountSeenAt: st.lastRealRobloxStatusAt || lastAccountSeenAt,
      lastRealRobloxStatusAt: st.lastRealRobloxStatusAt,
      heartbeatAgeSeconds: st.statusAgeSeconds != null ? st.statusAgeSeconds : seenAgeSeconds,
      accountPresenceLive: st.online,
      accountOnline: st.online,
      accountPresenceStatus: st.online ? 'online' : 'offline',
      accountPresenceReason: mappedReason,
      accountStatusReason: mappedReason,
      statusDecisionReason: st.statusDecisionReason,
      statusAgeSeconds: st.statusAgeSeconds,
      missedStatusReports: st.missedStatusReports,
      isStatusStale: st.isStatusStale,
      statusRevision: data.statusRevision != null ? Number(data.statusRevision) : null,
      statusReportId: data.statusReportId || null,
      statusSeq: data.statusSeq != null ? Number(data.statusSeq) : null,
      sessionId: data.statusSessionId || null,
      serverReceivedStatusAt: data.serverReceivedStatusAt || null,
      softGraceSeconds: st.softGraceSeconds,
      hardOfflineSeconds: st.hardOfflineSeconds,
    };
  }
  if (!lastAccountSeenAt) {
    return {
      ...base,
      accountPresenceLive: false,
      accountOnline: false,
      accountPresenceStatus: 'offline',
      accountPresenceReason: 'no_session',
      accountStatusReason: 'no_session',
    };
  }
  const recentSeen = seenAgeSeconds != null && seenAgeSeconds * 1000 < maxAgeMs;
  const loaderOnline = data.isOnline === true;
  const loaderOffline = data.isOnline === false;
  // A *confirmed* offline is an explicit offline snapshot (lastOfflineAt) that is
  // the most recent successful contact — i.e. no newer online/leaderstats/inventory
  // lane has reported in since. Non-status lanes (leaderstats fast-path, inventory)
  // that omit isOnline:true must NOT redline the account inside the grace window;
  // they refresh lastAccountSeenAt but never stamp lastOfflineAt, so they cannot
  // forge a confirmed-offline. This is what prevents the ~interval false-red.
  const lastOfflineAtMs = parseTimestampMs(data.lastOfflineAt);
  const lastSeenMs = parseTimestampMs(lastAccountSeenAt);
  const confirmedOffline = loaderOffline
    && lastOfflineAtMs != null
    && (lastSeenMs == null || lastOfflineAtMs >= lastSeenMs - 1000);
  const transientUploadFailure = isTransientServerUploadFailure(
    data?.lastFailureReason || data?.lastUploadRejectReason || data?.rejectReason,
    data?.lastUploadStatusCodeReturned || data?.lastUploadHttpStatus,
  );
  if (recentSeen) {
    if (confirmedOffline) {
      return {
        ...base,
        accountPresenceLive: false,
        accountOnline: false,
        accountPresenceStatus: 'offline',
        accountPresenceReason: 'client_offline',
        accountStatusReason: 'client_offline',
      };
    }
    return {
      ...base,
      accountPresenceLive: true,
      accountOnline: true,
      accountPresenceStatus: 'online',
      accountPresenceReason: transientUploadFailure
        ? 'last_success_within_grace'
        : (loaderOnline ? 'heartbeat' : 'loader_contact'),
      accountStatusReason: transientUploadFailure
        ? 'server_502_upload_retrying'
        : (loaderOnline ? 'heartbeat' : 'loader_contact'),
      uploadWarningReason: transientUploadFailure
        ? (data?.lastFailureReason || data?.lastUploadRejectReason || 'server_502_upload_retrying')
        : null,
    };
  }
  if (loaderOffline) {
    return {
      ...base,
      accountPresenceLive: false,
      accountOnline: false,
      accountPresenceStatus: 'offline',
      accountPresenceReason: 'client_offline',
      accountStatusReason: 'client_offline',
    };
  }
  return {
    ...base,
    accountPresenceLive: false,
    accountOnline: false,
    accountPresenceStatus: 'offline',
    accountPresenceReason: 'account_offline_timeout',
    accountStatusReason: 'account_offline_timeout',
  };
}

module.exports = {
  ACCOUNT_PRESENCE_GRACE_MS,
  ACCOUNT_ONLINE_THRESHOLD_MS,
  STATUS_CONTINUOUS_FAILURE_GRACE_MS,
  parseTimestampMs,
  syncAgeSecondsFromTimestamp,
  isTrustedClientBuild,
  resolveLastAccountSeenAt,
  deriveAccountPresenceStatus,
};
