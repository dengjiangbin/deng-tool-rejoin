'use strict';
/**
 * BLOCKER10Y — map in-game fish name TextColor3 hex to rarity tiers.
 * Colors align with Fish It UI tiers and tracker card CSS accents.
 */

const fishCatalog = require('./fishitFishCatalog');

/** Documented Fish It name-color → tier mapping (approximate UI TextColor3). */
const COLOR_TIER_RULES = [
  { tier: 'Secret', keys: ['#22d3ee', '#67e8f9', '#0e7490', '#06b6d4', '#0891b2'], dist: 48 },
  { tier: 'Mythic', keys: ['#f472b6', '#ec4899', '#db2777', '#ff69b4'], dist: 52 },
  { tier: 'Legendary', keys: ['#eab308', '#fbbf24', '#f59e0b', '#ca8a04', '#ffd700'], dist: 52 },
  { tier: 'Epic', keys: ['#c084fc', '#a855f7', '#9333ea', '#8b5cf6'], dist: 52 },
  { tier: 'Rare', keys: ['#60a5fa', '#3b82f6', '#2563eb', '#2196f3'], dist: 52 },
  { tier: 'Uncommon', keys: ['#4ade80', '#22c55e', '#16a34a', '#4caf50'], dist: 52 },
  { tier: 'Forgotten', keys: ['#818cf8', '#6366f1', '#4f46e5'], dist: 52 },
  { tier: 'Common', keys: ['#ffffff', '#e5e7eb', '#d1d5db', '#9ca3af', '#6b7280'], dist: 40 },
];

const TIER_ACCENT = {
  Common: '#9ca3af',
  Uncommon: '#4ade80',
  Rare: '#60a5fa',
  Epic: '#c084fc',
  Legendary: '#eab308',
  Mythic: '#f472b6',
  Secret: '#22d3ee',
  Forgotten: '#818cf8',
};

function _normHex(raw) {
  if (!raw || typeof raw !== 'string') return null;
  let h = raw.trim().toLowerCase();
  if (!h.startsWith('#')) h = `#${h}`;
  if (!/^#[0-9a-f]{6}$/.test(h)) return null;
  return h;
}

function _hexToRgb(hex) {
  const h = _normHex(hex);
  if (!h) return null;
  return {
    r: parseInt(h.slice(1, 3), 16),
    g: parseInt(h.slice(3, 5), 16),
    b: parseInt(h.slice(5, 7), 16),
  };
}

function _colorDistance(a, b) {
  const dr = a.r - b.r;
  const dg = a.g - b.g;
  const db = a.b - b.b;
  return Math.sqrt(dr * dr + dg * dg + db * db);
}

function resolveRarityFromUiColor(hex) {
  const norm = _normHex(hex);
  if (!norm) return null;
  const rgb = _hexToRgb(norm);
  if (!rgb) return null;

  for (const rule of COLOR_TIER_RULES) {
    for (const key of rule.keys) {
      if (norm === key) {
        return {
          tier: rule.tier,
          rarity: fishCatalog.normalizeRarity(rule.tier),
          mappedTierFromColor: rule.tier,
          inGameNameColor: norm,
          source: 'ui_name_color',
          confidence: 'ui_color_exact',
        };
      }
    }
  }

  let best = null;
  for (const rule of COLOR_TIER_RULES) {
    for (const key of rule.keys) {
      const ref = _hexToRgb(key);
      if (!ref) continue;
      const dist = _colorDistance(rgb, ref);
      if (dist <= rule.dist && (!best || dist < best.dist)) {
        best = { dist, tier: rule.tier, ref: key };
      }
    }
  }
  if (!best) return null;
  return {
    tier: best.tier,
    rarity: fishCatalog.normalizeRarity(best.tier),
    mappedTierFromColor: best.tier,
    inGameNameColor: norm,
    nearestReferenceColor: best.ref,
    colorDistance: Math.round(best.dist),
    source: 'ui_name_color',
    confidence: best.dist <= 24 ? 'ui_color_near' : 'ui_color_approx',
  };
}

function getRarityAccentColor(rarity) {
  const norm = fishCatalog.normalizeRarity(rarity);
  return norm ? (TIER_ACCENT[norm] || null) : null;
}

function cardRarityClassForTier(rarity) {
  const norm = fishCatalog.normalizeRarity(rarity);
  if (!norm) return null;
  return `rarity-${String(norm).toLowerCase()}`;
}

function buildRarityColorProofRow(item) {
  const finalRarity = item?.rarity && item.rarity !== 'Unknown' ? item.rarity : null;
  const crc = finalRarity ? cardRarityClassForTier(finalRarity) : null;
  return {
    itemId: item?.itemId || null,
    canonicalName: item?.canonicalName || item?.baseFishName || item?.name || null,
    finalRarity,
    finalRaritySource: item?.raritySource || null,
    inGameNameColor: item?.uiNameColor || null,
    mappedTierFromColor: item?.uiRarityFromColor || null,
    cardClass: crc,
    cardUsesFullRarityStyle: !!crc,
    rarityUnknownReason: finalRarity ? null : (item?.rarityNeedsData ? 'no_tier_source' : 'unknown_tier'),
  };
}

module.exports = {
  COLOR_TIER_RULES,
  TIER_ACCENT,
  resolveRarityFromUiColor,
  getRarityAccentColor,
  cardRarityClassForTier,
  buildRarityColorProofRow,
};
