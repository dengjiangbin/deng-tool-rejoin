'use strict';
/**
 * BLOCKER10Z — map in-game fish name TextColor3 hex to rarity tiers.
 * Uses tolerance-based nearest-color matching for Fish It bag UI labels.
 */

const fishCatalog = require('./fishitFishCatalog');

/** Documented Fish It name-color → tier mapping (approximate UI TextColor3). */
const COLOR_TIER_RULES = [
  { tier: 'Secret', keys: ['#22d3ee', '#67e8f9', '#0e7490', '#06b6d4', '#0891b2', '#00ffff', '#00ced1', '#2dd4bf'], dist: 55 },
  { tier: 'Mythic', keys: ['#f472b6', '#ec4899', '#db2777', '#ff69b4', '#ff00ff', '#e879f9', '#d946ef'], dist: 58 },
  { tier: 'Legendary', keys: ['#eab308', '#fbbf24', '#f59e0b', '#ca8a04', '#ffd700', '#ffa500', '#ff8800', '#ff9500', '#ffb347'], dist: 58 },
  { tier: 'Epic', keys: ['#c084fc', '#a855f7', '#9333ea', '#8b5cf6', '#b388ff', '#9c27b0'], dist: 58 },
  { tier: 'Rare', keys: ['#60a5fa', '#3b82f6', '#2563eb', '#2196f3', '#6080ff', '#4a90e2', '#5b9bd5'], dist: 58 },
  { tier: 'Uncommon', keys: ['#4ade80', '#22c55e', '#16a34a', '#4caf50', '#7dff3a', '#7df93a', '#00ff00', '#32cd32', '#00e676'], dist: 58 },
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
          mappedTierFromUi: rule.tier,
          inGameNameColor: norm,
          uiTextColor: norm,
          nearestReferenceColor: key,
          colorDistance: 0,
          source: 'inventory_ui_color',
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
  const conf = best.dist <= 20 ? 'ui_color_near' : (best.dist <= 40 ? 'ui_color_approx' : 'ui_color_weak');
  if (best.dist > 55) return null;
  return {
    tier: best.tier,
    rarity: fishCatalog.normalizeRarity(best.tier),
    mappedTierFromColor: best.tier,
    mappedTierFromUi: best.tier,
    inGameNameColor: norm,
    uiTextColor: norm,
    nearestReferenceColor: best.ref,
    closestTierColor: best.tier.toLowerCase(),
    colorDistance: Math.round(best.dist * 10) / 10,
    source: 'inventory_ui_color',
    confidence: conf,
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
    visibleName: item?.uiVisibleName || null,
    uiTextColor: item?.uiNameColor || null,
    closestTierColor: item?.uiClosestTierColor || (item?.uiRarityFromColor
      ? String(item.uiRarityFromColor).toLowerCase() : null),
    colorDistance: item?.uiColorDistance ?? null,
    mappedTierFromUi: item?.uiRarityFromColor || null,
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
