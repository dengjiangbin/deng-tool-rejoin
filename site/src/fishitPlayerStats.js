'use strict';

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
  const coinsText = clampText(raw.coinsText, 32);
  if (coinsText) out.coinsText = coinsText;
  else if (out.coins != null) out.coinsText = formatCompactStat(out.coins);
  const totalCaught = finiteNumber(raw.totalCaught);
  if (totalCaught != null) out.totalCaught = Math.max(0, totalCaught);
  const totalCaughtText = clampText(raw.totalCaughtText, 32);
  if (totalCaughtText) out.totalCaughtText = totalCaughtText;
  else if (out.totalCaught != null) out.totalCaughtText = formatGroupedStat(out.totalCaught);
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

function hasPlayerStatValues(stats) {
  if (!stats || typeof stats !== 'object') return false;
  return stats.coins != null
    || stats.totalCaught != null
    || !!stats.coinsText
    || !!stats.totalCaughtText
    || !!stats.rarestFishChance;
}

function mergePlayerStats(existing, incoming) {
  const next = sanitisePlayerStats(incoming);
  if (!next) return existing || null;
  if (!hasPlayerStatValues(next) && next.source === 'missing' && existing) return existing;
  if (!existing) return next;
  const merged = { ...existing, ...next };
  if (!hasPlayerStatValues(next) && existing.source && next.source === 'missing') {
    merged.source = existing.source;
  }
  return merged;
}

function displayCoins(stats) {
  if (!stats) return '—';
  if (stats.coinsText) return stats.coinsText;
  const compact = formatCompactStat(stats.coins);
  return compact || '—';
}

function displayTotalCaught(stats) {
  if (!stats) return '—';
  if (stats.totalCaughtText) return stats.totalCaughtText;
  const grouped = formatGroupedStat(stats.totalCaught);
  return grouped || '—';
}

function displayRarestFish(stats) {
  if (!stats || !stats.rarestFishChance) return '—';
  return stats.rarestFishChance;
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
  sanitisePlayerStats,
  mergePlayerStats,
  hasPlayerStatValues,
  displayCoins,
  displayTotalCaught,
  displayRarestFish,
  displayProgress,
  isProgressComplete,
  formatCompactStat,
  formatGroupedStat,
};
