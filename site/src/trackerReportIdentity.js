'use strict';

/**
 * Source-of-truth report identity + binary online/offline state machine for the
 * Fish-It Roblox tracker.
 *
 * The hard rule this module enforces:
 *   Online/offline truth and the "last real activity" timestamp may advance ONLY
 *   from a FRESH, UNIQUE Roblox-side report. They must NEVER advance from:
 *     - frontend polling / browser refresh / login
 *     - backend worker precompute time
 *     - read-API serve time
 *     - a duplicate / replayed / cached / stale report
 *
 * Every report carries (or is given) a monotonic identity:
 *   sessionId      unique per Roblox join/session
 *   <lane>Seq      increments every report on that lane
 *   <lane>ReportId sessionId + ":" + seq
 *   <lane>CapturedAt   when Roblox captured the report
 *   <lane>SentAt       when Roblox sent it (status lane only)
 *
 * The Roblox reporter (out-of-repo Luau) does not yet emit explicit identity
 * fields, so this module DERIVES a robust identity from the fields the live
 * reporter does send (runId/executionSessionId, uploadSeq, leaderstatsUploadSeq,
 * client capture timestamps) and is forward-compatible: if/when the reporter
 * starts sending explicit statusReportId/statusSeq/sessionId/capturedAt/sentAt,
 * those win automatically.
 */

// Expected upload cadence for every lane.
const STATUS_INTERVAL_MS = parseInt(process.env.TRACKER_STATUS_INTERVAL_MS || '60000', 10);
// Soft grace: still GREEN, but flagged stale (one or two missed reports tolerated).
const STATUS_SOFT_GRACE_MS = parseInt(process.env.TRACKER_STATUS_SOFT_GRACE_MS || '150000', 10);
// Hard offline: after this with no fresh unique status report → RED. ~3.25x interval
// so a single slow poll / 502 / delayed worker tick can never flip an in-game
// account red, while a real AFK/278 disconnect (reports stop) goes red within ~3 missed.
const STATUS_HARD_OFFLINE_MS = parseInt(process.env.TRACKER_STATUS_HARD_OFFLINE_MS || '195000', 10);

const LANE_FIELDS = {
  status: {
    seq: 'statusSeq',
    reportId: 'statusReportId',
    sessionId: 'statusSessionId',
    capturedAt: 'statusCapturedAt',
    sentAt: 'statusSentAt',
    revision: 'statusRevision',
    lastReal: 'lastRealRobloxStatusAt',
    serverReceived: 'serverReceivedStatusAt',
    decisionReason: 'statusIdentityReason',
    identitySource: 'reportIdentitySource',
  },
  leaderstats: {
    seq: 'leaderstatsSeq',
    reportId: 'leaderstatsReportId',
    sessionId: 'leaderstatsSessionId',
    capturedAt: 'leaderstatsCapturedAt',
    revision: 'leaderstatsRevision',
    lastReal: 'lastRealLeaderstatsAt',
    serverReceived: 'serverReceivedLeaderstatsAt',
    decisionReason: 'leaderstatsIdentityReason',
    identitySource: 'leaderstatsIdentitySource',
  },
  inventory: {
    seq: 'inventorySeq',
    reportId: 'inventoryReportId',
    sessionId: 'inventorySessionId',
    capturedAt: 'inventoryCapturedAt',
    hash: 'inventoryHash',
    revision: 'inventoryRevision',
    lastReal: 'lastRealInventoryAt',
    serverReceived: 'serverReceivedInventoryAt',
    decisionReason: 'inventoryIdentityReason',
    identitySource: 'inventoryIdentitySource',
  },
};

function parseTsMs(value) {
  if (value == null || value === '') return null;
  const ms = typeof value === 'number' ? value : new Date(value).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function toIso(ms) {
  const n = Number(ms);
  return Number.isFinite(n) ? new Date(n).toISOString() : null;
}

function firstString(...vals) {
  for (const v of vals) {
    if (v == null || v === '') continue;
    if (typeof v === 'string' || typeof v === 'number') return String(v).slice(0, 200);
  }
  return null;
}

function firstFiniteNumber(...vals) {
  for (const v of vals) {
    if (v == null || v === '') continue;
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

// Pull the explicit (reporter-provided) identity hints for a lane from the body.
function explicitIdentityHints(lane, body) {
  const b = body || {};
  const ps = b.playerStats && typeof b.playerStats === 'object' ? b.playerStats : {};
  if (lane === 'status') {
    // explicit = the NEW reporter contract (statusReportId, or sessionId+statusSeq).
    // runId/executionSessionId/uploadSeq are legacy-derived and do NOT count as explicit.
    const explicit = !!(b.statusReportId || (b.sessionId != null && b.statusSeq != null));
    return {
      explicit,
      sessionId: firstString(b.sessionId, b.statusSessionId, b.runId, b.executionSessionId, b.executionSession),
      seq: firstFiniteNumber(b.statusSeq, b.statusReportSeq, b.uploadSeq),
      reportId: firstString(b.statusReportId),
      capturedMs: parseTsMs(firstString(b.statusCapturedAt, b.capturedAt, b.statusObservedAt, b.clientCollectedAt)),
      sentAt: firstString(b.statusSentAt, b.sentAt),
    };
  }
  if (lane === 'leaderstats') {
    const explicit = !!(b.leaderstatsReportId || b.leaderstatsSeq != null);
    return {
      explicit,
      sessionId: firstString(b.sessionId, b.leaderstatsSessionId, b.runId, b.executionSessionId),
      seq: firstFiniteNumber(b.leaderstatsSeq, b.leaderstatsUploadSeq, b.uploadSeq),
      reportId: firstString(b.leaderstatsReportId),
      capturedMs: parseTsMs(firstString(b.leaderstatsCapturedAt, ps.statsAt, ps.observedAt, b.clientCollectedAt, b.capturedAt)),
      sentAt: null,
    };
  }
  // inventory
  const explicit = !!(b.inventoryReportId || b.inventorySeq != null || b.inventoryHash);
  return {
    explicit,
    sessionId: firstString(b.sessionId, b.inventorySessionId, b.runId, b.executionSessionId),
    seq: firstFiniteNumber(b.inventorySeq, b.uploadSeq),
    reportId: firstString(b.inventoryReportId),
    capturedMs: parseTsMs(firstString(b.inventoryCapturedAt, b.capturedAt, b.clientCollectedAt)),
    sentAt: null,
    hash: firstString(b.inventoryHash),
  };
}

function stableSessionId(lane, body, session) {
  const hints = explicitIdentityHints(lane, body);
  if (hints.sessionId) return hints.sessionId;
  const F = LANE_FIELDS[lane];
  if (session && session[F.sessionId]) return String(session[F.sessionId]);
  // Stable per-account fallback so the absence of a reporter-side sessionId does
  // not manufacture a "new session" on every upload. A real new Roblox join only
  // changes this when the reporter starts sending sessionId/runId.
  const acct = firstString(
    session && session.username,
    body && body.username,
    session && session.userId,
    body && body.userId,
  ) || 'unknown';
  return `acct:${acct.toLowerCase()}`;
}

/**
 * Classify an incoming report on a lane as FRESH (unique, advances truth) or
 * STALE (duplicate/replay/cached — must not advance truth).
 */
function classifyReport(lane, body, session, serverNowMs = Date.now()) {
  const F = LANE_FIELDS[lane];
  const hints = explicitIdentityHints(lane, body);
  const sessionId = stableSessionId(lane, body, session);
  const capturedAtMs = hints.capturedMs != null ? hints.capturedMs : serverNowMs;

  const prevSessionId = (session && session[F.sessionId]) || null;
  const prevSeq = firstFiniteNumber(session && session[F.seq]);
  const prevReportId = (session && session[F.reportId]) || null;
  const prevCapturedMs = parseTsMs(session && session[F.capturedAt]);
  const prevLastRealMs = parseTsMs(session && session[F.lastReal]);

  const explicitSeq = hints.seq;
  let seq;
  if (explicitSeq != null) seq = explicitSeq;
  else seq = (prevSeq != null ? prevSeq + 1 : 1);
  const reportId = hints.reportId || `${sessionId}:${seq}`;

  let fresh;
  let reason;
  if (prevSessionId == null && prevLastRealMs == null) {
    fresh = true;
    reason = 'first_report';
  } else if (sessionId !== prevSessionId) {
    // A new Roblox join. Accept it unless it is an obvious stale replay of an
    // OLDER capture than what we have already seen as real.
    if (prevLastRealMs != null && capturedAtMs < prevLastRealMs - 1000) {
      fresh = false;
      reason = 'stale_session_replay';
    } else {
      fresh = true;
      reason = 'new_session';
    }
  } else if (prevReportId != null && reportId === prevReportId) {
    fresh = false;
    reason = 'duplicate_report_id';
  } else if (explicitSeq != null) {
    if (prevSeq != null && explicitSeq <= prevSeq) {
      fresh = false;
      reason = 'stale_or_replayed_seq';
    } else {
      fresh = true;
      reason = 'seq_advanced';
    }
  } else if (prevCapturedMs != null && capturedAtMs <= prevCapturedMs) {
    fresh = false;
    reason = 'duplicate_capture';
  } else {
    fresh = true;
    reason = 'captured_advanced';
  }

  return {
    lane,
    fresh,
    reason,
    sessionId,
    seq,
    reportId,
    capturedAtMs,
    sentAt: hints.sentAt || null,
    hash: hints.hash || null,
    identitySource: hints.explicit ? 'client_explicit' : 'backend_derived',
  };
}

/**
 * Produce the session field updates for a lane report. When fresh, advances the
 * lane identity + lastReal timestamp + revision counter. When stale, advances
 * NOTHING except the debug reason (so a duplicate/replay can never reset age).
 */
function applyReport(lane, session, body, serverNowMs = Date.now()) {
  const F = LANE_FIELDS[lane];
  const c = classifyReport(lane, body, session, serverNowMs);
  const updates = {};
  updates[F.decisionReason] = c.reason;
  // identitySource reflects the LAST report on this lane (explicit vs derived),
  // independent of freshness so the API always shows the current reporter contract.
  if (F.identitySource) updates[F.identitySource] = c.identitySource;
  if (c.fresh) {
    // lastReal = when Roblox captured the report, never in the future, never
    // older than what we already trusted (monotonic).
    const prevLastRealMs = parseTsMs(session && session[F.lastReal]);
    let lastRealMs = Math.min(c.capturedAtMs, serverNowMs);
    if (prevLastRealMs != null && lastRealMs < prevLastRealMs) lastRealMs = prevLastRealMs;
    updates[F.sessionId] = c.sessionId;
    updates[F.seq] = c.seq;
    updates[F.reportId] = c.reportId;
    updates[F.capturedAt] = toIso(c.capturedAtMs);
    updates[F.serverReceived] = toIso(serverNowMs);
    updates[F.lastReal] = toIso(lastRealMs);
    updates[F.revision] = (firstFiniteNumber(session && session[F.revision]) || 0) + 1;
    if (F.sentAt && c.sentAt) updates[F.sentAt] = c.sentAt;
    if (F.hash && c.hash) updates[F.hash] = c.hash;
  }
  return {
    updates,
    lane,
    fresh: c.fresh,
    reason: c.reason,
    reportId: c.reportId,
    seq: c.seq,
    sessionId: c.sessionId,
    capturedAtMs: c.capturedAtMs,
    identitySource: c.identitySource,
  };
}

/**
 * Reinforce the STATUS truth from a fresh online report on another lane
 * (inventory / leaderstats). An online-asserting upload that produced a fresh
 * unique identity on its own lane IS a valid "Roblox is live right now" signal,
 * so it advances lastRealRobloxStatusAt + statusRevision — WITHOUT overwriting
 * the dedicated status identity (statusSeq/statusReportId/statusSessionId stay
 * owned by the tracker_status heartbeat lane). This makes the status lane
 * resilient (it does not DEPEND on the heartbeat lane) while never letting a
 * stale/replayed report fake freshness (the caller only invokes this when the
 * lane report was fresh AND the upload asserted isOnline===true).
 */
function advanceStatusTruth(session, capturedAtMs, serverNowMs = Date.now(), source = 'lane') {
  const prevLastRealMs = parseTsMs(session && session.lastRealRobloxStatusAt);
  let lastRealMs = Math.min(
    Number.isFinite(capturedAtMs) ? capturedAtMs : serverNowMs,
    serverNowMs,
  );
  if (prevLastRealMs != null && lastRealMs < prevLastRealMs) lastRealMs = prevLastRealMs;
  return {
    lastRealRobloxStatusAt: toIso(lastRealMs),
    serverReceivedStatusAt: toIso(serverNowMs),
    statusRevision: (firstFiniteNumber(session && session.statusRevision) || 0) + 1,
    statusIdentityReason: `online_${source}_report`,
  };
}

/**
 * Binary online/offline state machine. Truth is derived ONLY from
 * lastRealRobloxStatusAt (the capture time of the last fresh unique status
 * report) versus now. Two states only: green (online) / red (offline).
 */
function evaluateStatusState(session, nowMs = Date.now(), opts = {}) {
  const softGraceMs = opts.softGraceMs != null ? opts.softGraceMs : STATUS_SOFT_GRACE_MS;
  const hardOfflineMs = opts.hardOfflineMs != null ? opts.hardOfflineMs : STATUS_HARD_OFFLINE_MS;
  const lastRealMs = parseTsMs(session && session.lastRealRobloxStatusAt);
  const lastRealIso = lastRealMs != null ? toIso(lastRealMs) : null;

  if (lastRealMs == null) {
    return {
      online: false,
      status: 'offline',
      statusColor: 'red',
      statusAgeSeconds: null,
      lastRealRobloxStatusAt: null,
      statusDecisionReason: 'no_status_report',
      missedStatusReports: null,
      isStatusStale: true,
      softGraceSeconds: Math.floor(softGraceMs / 1000),
      hardOfflineSeconds: Math.floor(hardOfflineMs / 1000),
    };
  }

  const ageMs = Math.max(0, nowMs - lastRealMs);
  const ageSeconds = Math.floor(ageMs / 1000);
  const missedStatusReports = Math.max(0, Math.floor(ageMs / STATUS_INTERVAL_MS));

  // A *confirmed* offline is an explicit client offline (isOnline:false) whose
  // lastOfflineAt is at least as new as the last real status report — i.e. the
  // reporter itself told us the account went offline (AFK/278 detected client
  // side). Non-status lanes cannot forge this.
  const lastOfflineMs = parseTsMs(session && session.lastOfflineAt);
  const confirmedOffline = session && session.isOnline === false
    && lastOfflineMs != null
    && lastOfflineMs >= lastRealMs - 1000;

  const base = {
    statusAgeSeconds: ageSeconds,
    lastRealRobloxStatusAt: lastRealIso,
    missedStatusReports,
    softGraceSeconds: Math.floor(softGraceMs / 1000),
    hardOfflineSeconds: Math.floor(hardOfflineMs / 1000),
  };

  if (confirmedOffline) {
    return {
      ...base,
      online: false,
      status: 'offline',
      statusColor: 'red',
      statusDecisionReason: 'client_offline',
      isStatusStale: true,
    };
  }

  if (ageMs <= hardOfflineMs) {
    return {
      ...base,
      online: true,
      status: 'online',
      statusColor: 'green',
      statusDecisionReason: ageMs <= softGraceMs ? 'fresh_status_report' : 'within_grace_missed_report',
      isStatusStale: ageMs > softGraceMs,
    };
  }

  return {
    ...base,
    online: false,
    status: 'offline',
    statusColor: 'red',
    statusDecisionReason: 'hard_offline_timeout',
    isStatusStale: true,
  };
}

// Has any identity-gated status truth been recorded yet for this session?
function hasRealStatusIdentity(session) {
  return parseTsMs(session && session.lastRealRobloxStatusAt) != null;
}

module.exports = {
  STATUS_INTERVAL_MS,
  STATUS_SOFT_GRACE_MS,
  STATUS_HARD_OFFLINE_MS,
  LANE_FIELDS,
  parseTsMs,
  toIso,
  explicitIdentityHints,
  classifyReport,
  applyReport,
  advanceStatusTruth,
  evaluateStatusState,
  hasRealStatusIdentity,
};
