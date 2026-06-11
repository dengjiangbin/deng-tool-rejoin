'use strict';

const TRUSTED_PLAYERSTATS_BUILD_MARKS = ['BLOCKER10ZT5', 'BLOCKER10ZT4', 'BLOCKER10ZT3', 'BLOCKER10ZW'];

function clampText(value, maxLen) {
  if (value == null) return null;
  const s = String(value).trim();
  if (!s) return null;
  return s.slice(0, maxLen);
}

function finiteNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function formatCompactStat(value) {
  const n = finiteNumber(value);
  if (n == null) return null;
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, '')}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
  return String(Math.max(0, Math.floor(n)));
}

function formatGroupedStat(value) {
  const n = finiteNumber(value);
  if (n == null) return null;
  return Math.max(0, Math.floor(n)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
}

function normaliseProgress(raw) {
  if (raw == null) return null;
  if (typeof raw === 'string') {
    const m = raw.trim().match(/^(\d+)\s*\/\s*(\d+)$/);
    if (m) return { current: Number(m[1]), max: Number(m[2]) };
    return null;
  }
  if (typeof raw !== 'object') return null;
  const current = finiteNumber(raw.current ?? raw.progress ?? raw.done ?? raw.value);
  const max = finiteNumber(raw.max ?? raw.total ?? raw.goal ?? raw.target);
  if (current == null || max == null || max <= 0) return null;
  return { current: Math.max(0, Math.floor(current)), max: Math.max(0, Math.floor(max)) };
}

function sanitisePlayerStats(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const out = {};
  const coins = finiteNumber(raw.coins);
  if (coins != null) out.coins = Math.max(0, coins);
  const totalCaught = finiteNumber(raw.totalCaught);
  if (totalCaught != null) out.totalCaught = Math.max(0, totalCaught);
  if (out.coins != null) out.coinsText = formatCompactStat(out.coins);
  else {
    const coinsText = clampText(raw.coinsText, 32);
    if (coinsText) out.coinsText = coinsText;
  }
  if (out.totalCaught != null) out.totalCaughtText = formatGroupedStat(out.totalCaught);
  else {
    const totalCaughtText = clampText(raw.totalCaughtText, 32);
    if (totalCaughtText) out.totalCaughtText = totalCaughtText;
  }
  const rarestFishChance = clampText(raw.rarestFishChance ?? raw.rarestFish, 32);
  if (rarestFishChance) out.rarestFishChance = rarestFishChance;
  const ruin = normaliseProgress(raw.ruin);
  if (ruin) out.ruin = ruin;
  const artifact = normaliseProgress(raw.artifact);
  if (artifact) out.artifact = artifact;
  const statsAt = clampText(raw.statsAt ?? raw.updatedAt, 40);
  if (statsAt) out.statsAt = statsAt;
  const source = clampText(raw.source, 32);
  if (source) out.source = source;
  const observedAt = finiteNumber(raw.observedAt);
  if (observedAt != null) out.observedAt = Math.max(0, Math.floor(observedAt));
  const build = clampText(raw.build, 64);
  if (build) out.build = build;
  return Object.keys(out).length ? out : null;
}

function sanitisePlayerStatsDebug(raw) {
  if (!raw || typeof raw !== 'object' || raw.enabled !== true) return null;
  const out = { enabled: true };
  const source = clampText(raw.source, 32);
  if (source) out.source = source;
  const build = clampText(raw.build, 64);
  if (build) out.build = build;
  if (raw.rawKeysFound && typeof raw.rawKeysFound === 'object') {
    out.rawKeysFound = {
      replion: Array.isArray(raw.rawKeysFound.replion) ? raw.rawKeysFound.replion.slice(0, 40) : [],
      leaderstats: Array.isArray(raw.rawKeysFound.leaderstats) ? raw.rawKeysFound.leaderstats.slice(0, 40) : [],
    };
  }
  out.rawCoinsValue = clampText(raw.rawCoinsValue, 64);
  out.rawTotalCaughtValue = clampText(raw.rawTotalCaughtValue, 64);
  out.rawRarestFishValue = clampText(raw.rawRarestFishValue, 64);
  out.coinsSource = clampText(raw.coinsSource, 32);
  out.caughtSource = clampText(raw.caughtSource, 32);
  out.rarestSource = clampText(raw.rarestSource, 32);
  if (raw.coinProbe && typeof raw.coinProbe === 'object') {
    out.coinProbe = {
      source: clampText(raw.coinProbe.source, 32),
      matchedPath: clampText(raw.coinProbe.matchedPath, 64),
      matchedKey: clampText(raw.coinProbe.matchedKey, 48),
      rawValue: clampText(raw.coinProbe.rawValue, 64),
      parsedValue: finiteNumber(raw.coinProbe.parsedValue),
      candidateKeys: Array.isArray(raw.coinProbe.candidateKeys)
        ? raw.coinProbe.candidateKeys.slice(0, 40).map((k) => clampText(k, 48)).filter(Boolean)
        : [],
      leaderstatsChildren: Array.isArray(raw.coinProbe.leaderstatsChildren)
        ? raw.coinProbe.leaderstatsChildren.slice(0, 40).map((row) => {
          if (!row || typeof row !== 'object') return null;
          const name = clampText(row.name, 48);
          const value = clampText(row.value, 64);
          return name ? { name, value: value || null } : null;
        }).filter(Boolean)
        : [],
    };
  }
  return out;
}

function hasPlayerStatValues(stats) {
  if (!stats || typeof stats !== 'object') return false;
  return stats.coins != null
    || stats.totalCaught != null
    || !!stats.coinsText
    || !!stats.totalCaughtText
    || !!stats.rarestFishChance;
}

function isTrustedPlayerStatsBuild(build) {
  return typeof build === 'string'
    && TRUSTED_PLAYERSTATS_BUILD_MARKS.some((mark) => build.includes(mark));
}

function isTrustedPlayerStatsSource(source) {
  return source === 'replion' || source === 'leaderstats' || source === 'missing';
}

function isTrustedPlayerStats(stats) {
  if (!stats || typeof stats !== 'object') return false;
  if (!isTrustedPlayerStatsBuild(stats.build)) return false;
  return isTrustedPlayerStatsSource(stats.source);
}

function displayablePlayerStats(stats) {
  const s = sanitisePlayerStats(stats);
  if (!s || !isTrustedPlayerStats(s)) return null;
  if (s.source === 'missing') {
    if (!hasPlayerStatValues(s)) {
      return {
        source: 'missing',
        build: s.build,
        observedAt: s.observedAt,
      };
    }
    return null;
  }
  if (!hasPlayerStatValues(s)) return null;
  return s;
}

function isAcceptableIncomingPlayerStats(stats) {
  return isTrustedPlayerStats(stats)
    || (stats && stats.source === 'missing' && isTrustedPlayerStatsBuild(stats.build));
}

function mergePlayerStats(existing, incoming, opts = {}) {
  const trustedExisting = isTrustedPlayerStats(existing) ? existing : null;
  const next = sanitisePlayerStats(incoming);
  if (!next) return trustedExisting || null;
  if (!isAcceptableIncomingPlayerStats(next)) return trustedExisting || null;
  const isLiveRoblox = !!(opts && opts.isLiveRoblox);
  if (!hasPlayerStatValues(next) && next.source === 'missing') {
    if (isLiveRoblox) return next;
    if (trustedExisting) return trustedExisting;
    return next;
  }
  if (!trustedExisting) {
    if (next.coins != null) next.coinsText = formatCompactStat(next.coins);
    if (next.totalCaught != null) next.totalCaughtText = formatGroupedStat(next.totalCaught);
    return next;
  }
  const merged = { ...trustedExisting, ...next };
  if (merged.coins != null) merged.coinsText = formatCompactStat(merged.coins);
  if (merged.totalCaught != null) merged.totalCaughtText = formatGroupedStat(merged.totalCaught);
  return merged;
}

function normalizePlayerStatsForApi(raw) {
  const s = displayablePlayerStats(raw);
  if (!s) return null;
  const out = { ...s };
  if (out.coins != null) out.coinsText = formatCompactStat(out.coins);
  if (out.totalCaught != null) out.totalCaughtText = formatGroupedStat(out.totalCaught);
  return out;
}

function displayCoins(stats) {
  const s = displayablePlayerStats(stats);
  if (!s || s.source === 'missing') return '—';
  if (s.coins != null) {
    const compact = formatCompactStat(s.coins);
    if (compact) return compact;
  }
  if (s.coinsText) return s.coinsText;
  return '—';
}

function displayTotalCaught(stats) {
  const s = displayablePlayerStats(stats);
  if (!s || s.source === 'missing') return '—';
  if (s.totalCaught != null) {
    const grouped = formatGroupedStat(s.totalCaught);
    if (grouped) return grouped;
  }
  if (s.totalCaughtText) return s.totalCaughtText;
  return '—';
}

function displayRarestFish(stats) {
  const s = displayablePlayerStats(stats);
  if (!s || s.source === 'missing' || !s.rarestFishChance) return '—';
  return s.rarestFishChance;
}

function displayProgress(stats, key) {
  const block = stats && stats[key];
  if (!block) return '—';
  return `${block.current}/${block.max}`;
}

function isProgressComplete(stats, key) {
  const block = stats && stats[key];
  return !!(block && block.max > 0 && block.current >= block.max);
}

module.exports = {
  TRUSTED_PLAYERSTATS_BUILD_MARKS,
  TRUSTED_PLAYERSTATS_BUILD_MARK: TRUSTED_PLAYERSTATS_BUILD_MARKS[0],
  sanitisePlayerStats,
  sanitisePlayerStatsDebug,
  mergePlayerStats,
  hasPlayerStatValues,
  isTrustedPlayerStatsBuild,
  isTrustedPlayerStatsSource,
  isTrustedPlayerStats,
  isAcceptableIncomingPlayerStats,
  displayablePlayerStats,
  normalizePlayerStatsForApi,
  displayCoins,
  displayTotalCaught,
  displayRarestFish,
  displayProgress,
  isProgressComplete,
  formatCompactStat,
  formatGroupedStat,
};
