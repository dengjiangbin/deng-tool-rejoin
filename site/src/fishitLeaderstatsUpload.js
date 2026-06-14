'use strict';

const playerStatsStore = require('./fishitPlayerStats');

const DEFAULT_INTERVAL_SECONDS = 60;
const DEFAULT_GRACE_SECONDS = 15;

function clampText(value, max = 120) {
  if (value == null) return null;
  const s = String(value).trim();
  return s ? s.slice(0, max) : null;
}

function isLiveRobloxBody(body) {
  return body && (body.clientOrigin === 'roblox_tracker' || body.evidenceSourceMode === 'live_roblox');
}

function statsValuesChanged(prev, next) {
  const norm = (s) => ({
    coins: s && s.coins != null ? Number(s.coins) : null,
    totalCaught: s && s.totalCaught != null ? Number(s.totalCaught) : null,
    rarestFishChance: s && s.rarestFishChance ? String(s.rarestFishChance) : null,
  });
  const a = norm(prev);
  const b = norm(next);
  return a.totalCaught !== b.totalCaught
    || a.coins !== b.coins
    || a.rarestFishChance !== b.rarestFishChance;
}

function extractLeaderstatsProof(body) {
  const ps = body?.playerStats && typeof body.playerStats === 'object' ? body.playerStats : {};
  const dbg = body?.playerStatsDebug && typeof body.playerStatsDebug === 'object'
    ? body.playerStatsDebug
    : (body?.leaderstatsProofCompact && typeof body.leaderstatsProofCompact === 'object'
      ? body.leaderstatsProofCompact
      : {});
  const keys = Array.isArray(body?.leaderstatsKeys)
    ? body.leaderstatsKeys
    : (Array.isArray(dbg.leaderstatKeys) ? dbg.leaderstatKeys : []);
  return {
    leaderstatsFound: body?.leaderstatsFound === true
      || ps.source === 'leaderstats'
      || dbg.source === 'leaderstats'
      || body?.leaderstatsReady === true,
    leaderstatsPath: clampText(
      body?.leaderstatsPath || dbg.matchedPath || dbg.coinProbe?.matchedPath,
      96,
    ),
    coin: ps.coins != null ? ps.coins : (dbg.rawCoinsValue != null ? dbg.rawCoinsValue : null),
    totalCaught: ps.totalCaught != null
      ? ps.totalCaught
      : (dbg.rawTotalCaughtValue != null ? dbg.rawTotalCaughtValue : null),
    leaderstatsKeys: keys.slice(0, 40).map((k) => clampText(k, 48)).filter(Boolean),
    uploadSeq: Number.isFinite(Number(body?.uploadSeq)) ? Number(body.uploadSeq) : null,
    clientCollectedAt: clampText(body?.clientCollectedAt || ps.observedAt || ps.statsAt, 40),
  };
}

function evaluateIncomingLeaderstats(body, existing, now) {
  const incoming = playerStatsStore.enrichIncomingPlayerStats(body?.playerStats, {
    trackerBuild: body?.trackerBuild,
    playerStatsDebug: body?.playerStatsDebug || body?.leaderstatsProofCompact,
    isLiveRoblox: isLiveRobloxBody(body),
  });
  const proof = extractLeaderstatsProof(body);
  const hasValues = !!(incoming
    && playerStatsStore.hasPlayerStatValues(incoming)
    && incoming.source !== 'missing');
  const trusted = playerStatsStore.isTrustedPlayerStats(incoming);

  if (hasValues && trusted) {
    const normalized = playerStatsStore.normalizePlayerStatsForApi(incoming);
    return {
      ok: true,
      missingReason: null,
      incoming,
      normalized,
      proof,
      lastValidLeaderstats: normalized,
      lastValidLeaderstatsAt: now,
    };
  }

  let missingReason = 'missing_leaderstats';
  if (!body?.playerStats && !body?.playerStatsDebug && !body?.leaderstatsProofCompact) {
    missingReason = 'missing_leaderstats_payload';
  } else if (incoming?.source === 'missing') {
    missingReason = 'leaderstats_source_missing';
  } else if (!trusted) {
    missingReason = 'leaderstats_untrusted';
  } else if (!hasValues) {
    missingReason = 'leaderstats_empty';
  }

  return {
    ok: false,
    missingReason,
    incoming: incoming || null,
    normalized: null,
    proof,
    lastValidLeaderstats: existing?.lastValidLeaderstats || null,
    lastValidLeaderstatsAt: existing?.lastValidLeaderstatsAt || null,
  };
}

function applyLeaderstatsUploadFields(existing, body, now, opts = {}) {
  const isHeartbeat = opts.isHeartbeat === true;
  const evalResult = evaluateIncomingLeaderstats(body, existing, now);
  const out = { leaderstatsProof: evalResult.proof };

  if (isHeartbeat) {
    return {
      ...out,
      leaderstatsUploadOk: existing?.leaderstatsUploadOk === true,
      leaderstatsUploadedAt: existing?.leaderstatsUploadedAt || null,
      leaderstatsUploadSeq: existing?.leaderstatsUploadSeq ?? null,
      leaderstatsMissingReason: existing?.leaderstatsMissingReason || null,
      lastValidLeaderstats: existing?.lastValidLeaderstats || null,
      lastValidLeaderstatsAt: existing?.lastValidLeaderstatsAt || null,
    };
  }

  const uploadSeq = evalResult.proof.uploadSeq;
  if (evalResult.ok) {
    out.leaderstatsUploadOk = true;
    out.leaderstatsUploadedAt = now;
    out.leaderstatsUploadSeq = uploadSeq;
    out.leaderstatsMissingReason = null;
    out.lastValidLeaderstats = evalResult.lastValidLeaderstats;
    out.lastValidLeaderstatsAt = now;
    out.statsRedSince = null;
    out.lastRequiredUploadAt = now;
    out.requiredOk = true;
    if (evalResult.incoming) {
      const merged = playerStatsStore.mergePlayerStats(existing?.playerStats, evalResult.incoming, {
        isLiveRoblox: isLiveRobloxBody(body),
      });
      const stored = playerStatsStore.isTrustedPlayerStats(merged) ? merged : null;
      if (stored) {
        const changed = statsValuesChanged(existing?.playerStats, stored);
        out.playerStatsChanged = changed;
        out.sameValuesFreshSync = !changed;
        out.playerStats = { ...stored, statsAt: now };
        out.playerStatsUpdatedAt = now;
        out.lastStatsUploadAt = now;
        if (changed) {
          out.lastStatsChangeAt = now;
        }
      }
    }
    const debug = playerStatsStore.sanitisePlayerStatsDebug(body?.playerStatsDebug);
    if (debug && out.playerStats && playerStatsStore.isTrustedPlayerStats(out.playerStats)) {
      out.playerStatsDebug = debug;
    }
    return out;
  }

  out.leaderstatsUploadOk = false;
  out.leaderstatsUploadedAt = now;
  out.leaderstatsUploadSeq = uploadSeq;
  out.leaderstatsMissingReason = evalResult.missingReason;
  out.lastValidLeaderstats = existing?.lastValidLeaderstats || null;
  out.lastValidLeaderstatsAt = existing?.lastValidLeaderstatsAt || null;
  if (existing?.playerStats && playerStatsStore.isTrustedPlayerStats(existing.playerStats)) {
    out.playerStats = existing.playerStats;
    out.playerStatsUpdatedAt = existing.playerStatsUpdatedAt || null;
    out.lastStatsUploadAt = existing.lastStatsUploadAt || null;
    out.lastStatsChangeAt = existing.lastStatsChangeAt || null;
  }
  if (!existing?.statsRedSince) {
    out.statsRedSince = now;
  }
  return out;
}

function leaderstatsUploadTimestamp(data) {
  if (!data || data.leaderstatsUploadOk !== true) return null;
  return data.leaderstatsUploadedAt || data.lastStatsUploadAt || data.playerStatsUpdatedAt || null;
}

function deriveLeaderstatsUploadStatus(data, opts = {}) {
  const intervalSeconds = Number(data?.intervalSeconds) > 0
    ? Number(data.intervalSeconds)
    : (Number(data?.uploadIntervalSeconds) > 0
      ? Number(data.uploadIntervalSeconds)
      : DEFAULT_INTERVAL_SECONDS);
  const graceSeconds = Number(data?.graceSeconds) >= 0
    ? Number(data.graceSeconds)
    : DEFAULT_GRACE_SECONDS;
  const serverNowMs = opts.serverNowMs != null ? opts.serverNowMs : Date.now();
  const ts = leaderstatsUploadTimestamp(data);
  const ageSeconds = ts
    ? Math.floor((serverNowMs - new Date(ts).getTime()) / 1000)
    : null;
  const deadlineMs = ts ? new Date(ts).getTime() + (intervalSeconds + graceSeconds) * 1000 : null;
  const fresh = !!(data?.leaderstatsUploadOk === true && ts && serverNowMs <= deadlineMs);

  return {
    leaderstatsUploadedAt: data?.leaderstatsUploadedAt || null,
    leaderstatsUploadOk: data?.leaderstatsUploadOk === true,
    leaderstatsUploadSeq: data?.leaderstatsUploadSeq ?? null,
    leaderstatsMissingReason: data?.leaderstatsMissingReason || null,
    lastValidLeaderstats: data?.lastValidLeaderstats || null,
    lastValidLeaderstatsAt: data?.lastValidLeaderstatsAt || null,
    lastStatsUploadAt: ts,
    statsUploadAgeSeconds: ageSeconds != null && ageSeconds >= 0 ? ageSeconds : null,
    statsUploadFresh: fresh,
    statsUploadStatus: fresh ? 'fresh' : (ts ? 'stale' : 'never'),
    statsRedSince: fresh
      ? null
      : (data?.statsRedSince || (deadlineMs ? new Date(deadlineMs).toISOString() : null)),
    intervalSeconds,
    graceSeconds,
  };
}

function resolveLeaderstatsFreshnessTimestamp(data) {
  return leaderstatsUploadTimestamp(data);
}

function resolvePlayerStatsForLiveDisplay(data, resolvePlayerStatsForApi) {
  if (data?.leaderstatsUploadOk === true && data?.playerStats) {
    const current = typeof resolvePlayerStatsForApi === 'function'
      ? resolvePlayerStatsForApi(data.playerStats)
      : playerStatsStore.normalizePlayerStatsForApi(data.playerStats);
    if (current) return current;
  }
  if (data?.lastValidLeaderstats && typeof data.lastValidLeaderstats === 'object') {
    return data.lastValidLeaderstats;
  }
  if (typeof resolvePlayerStatsForApi === 'function') {
    return resolvePlayerStatsForApi(data?.playerStats);
  }
  return playerStatsStore.normalizePlayerStatsForApi(data?.playerStats);
}

function logLeaderstatsUploadProof(username, evalResult, now) {
  const user = clampText(username, 32) || 'unknown';
  const seq = evalResult.proof?.uploadSeq != null ? evalResult.proof.uploadSeq : '?';
  if (evalResult.ok) {
    const coin = evalResult.normalized?.coins != null ? evalResult.normalized.coins : '?';
    const totalCaught = evalResult.normalized?.totalCaught != null
      ? evalResult.normalized.totalCaught
      : '?';
    const path = evalResult.proof?.leaderstatsPath || 'n/a';
    console.log(
      `LEADERSTATS_UPLOAD_OK user=${user} seq=${seq} coin=${coin} totalCaught=${totalCaught}`
      + ` path=${path} receivedAt=${now}`,
    );
    return;
  }
  const reason = evalResult.missingReason || 'missing_leaderstats';
  console.log(
    `LEADERSTATS_UPLOAD_MISSING user=${user} seq=${seq} reason=${reason} status=red`,
  );
}

function resolveUploadSequence(data) {
  if (Number.isFinite(Number(data?.uploadSeq))) return Number(data.uploadSeq);
  if (Number.isFinite(Number(data?.leaderstatsUploadSeq))) return Number(data.leaderstatsUploadSeq);
  return null;
}

function publicLeaderstatsFields(data) {
  if (!data || typeof data !== 'object') return {};
  const uploadSequence = resolveUploadSequence(data);
  return {
    leaderstatsUploadedAt: data.leaderstatsUploadedAt || null,
    leaderstatsUploadOk: data.leaderstatsUploadOk === true,
    leaderstatsUploadSeq: data.leaderstatsUploadSeq ?? null,
    leaderstatsMissingReason: data.leaderstatsMissingReason || null,
    lastValidLeaderstatsAt: data.lastValidLeaderstatsAt || null,
    lastValidLeaderstats: data.lastValidLeaderstats || null,
    uploadSequence,
    syncSequence: uploadSequence,
    lastRequiredUploadAt: data.lastRequiredUploadAt || data.leaderstatsUploadedAt || null,
    requiredOk: data.requiredOk === true || data.leaderstatsUploadOk === true,
    playerStatsChanged: data.playerStatsChanged === true,
    inventoryChanged: data.inventoryChanged === true,
    sameValuesFreshSync: data.sameValuesFreshSync === true,
    serverReceivedAt: data.lastUploadReceivedAt || data.serverReceivedAt || null,
    lastSuccessfulUploadAt: data.lastSuccessfulUploadAt || data.leaderstatsUploadedAt || null,
  };
}

module.exports = {
  DEFAULT_INTERVAL_SECONDS,
  DEFAULT_GRACE_SECONDS,
  extractLeaderstatsProof,
  evaluateIncomingLeaderstats,
  applyLeaderstatsUploadFields,
  leaderstatsUploadTimestamp,
  deriveLeaderstatsUploadStatus,
  resolveLeaderstatsFreshnessTimestamp,
  resolvePlayerStatsForLiveDisplay,
  logLeaderstatsUploadProof,
  publicLeaderstatsFields,
  resolveUploadSequence,
  statsValuesChanged,
};
