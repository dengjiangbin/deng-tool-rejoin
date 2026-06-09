'use strict';

const fs = require('fs');
const path = require('path');

const OVERRIDES_PATH = path.join(__dirname, '..', 'data', 'fishit_manual_rarity_overrides.json');

const RARITY_TO_TIER = {
  Common: 1,
  Uncommon: 2,
  Rare: 3,
  Epic: 4,
  Legendary: 5,
  Mythic: 6,
  Secret: 7,
  Forgotten: 8,
};

const TIER_TO_RARITY = Object.fromEntries(
  Object.entries(RARITY_TO_TIER).map(([name, tier]) => [String(tier), name]),
);

let _overrides = null;

function normalizeFishName(name) {
  return String(name || '').trim().replace(/\s+/g, ' ');
}

function loadOverrides() {
  if (_overrides) return _overrides;
  try {
    const raw = JSON.parse(fs.readFileSync(OVERRIDES_PATH, 'utf8'));
    _overrides = {
      version: raw.version || 1,
      updatedAt: raw.updatedAt || null,
      byName: raw.byName && typeof raw.byName === 'object' ? raw.byName : {},
      byItemId: raw.byItemId && typeof raw.byItemId === 'object' ? raw.byItemId : {},
    };
  } catch {
    _overrides = { version: 1, updatedAt: null, byName: {}, byItemId: {} };
  }
  return _overrides;
}

function tierFromRarityName(rarity) {
  const key = normalizeFishName(rarity);
  return RARITY_TO_TIER[key] || null;
}

function resolvePublicFishRarity(item, tierToRarityFn) {
  const overrides = loadOverrides();
  const itemId = item?.itemId != null ? String(item.itemId).trim() : '';
  const name = normalizeFishName(item?.baseFishName || item?.baseName || item?.name);

  if (itemId && overrides.byItemId[itemId]) {
    const rarity = overrides.byItemId[itemId];
    return {
      rarity,
      tier: tierFromRarityName(rarity) || item?.tier || 1,
      raritySource: 'manual_rarity_override',
    };
  }

  if (name && overrides.byName[name]) {
    const rarity = overrides.byName[name];
    return {
      rarity,
      tier: tierFromRarityName(rarity) || item?.tier || 1,
      raritySource: 'manual_rarity_override',
    };
  }

  const uploadedRarity = item?.rarity && item.rarity !== 'Unknown'
    ? String(item.rarity).trim()
    : null;
  if (uploadedRarity) {
    const tierNum = Number(item?.tier);
    return {
      rarity: uploadedRarity,
      tier: Number.isFinite(tierNum) && tierNum > 0
        ? Math.floor(tierNum)
        : (tierFromRarityName(uploadedRarity) || 1),
      raritySource: 'playerdata_itemutility_tier',
    };
  }

  const tierNum = Number(item?.tier);
  if (Number.isFinite(tierNum) && tierNum > 0) {
    const rarity = typeof tierToRarityFn === 'function'
      ? tierToRarityFn(tierNum)
      : (TIER_TO_RARITY[String(Math.floor(tierNum))] || 'Common');
    if (rarity && rarity !== 'Unknown') {
      return {
        rarity,
        tier: Math.floor(tierNum),
        raritySource: 'playerdata_itemutility_tier',
      };
    }
  }

  return {
    rarity: 'Common',
    tier: 1,
    raritySource: 'safe_default_common',
  };
}

function countMissingPublicRarity(fishItems = []) {
  return fishItems.filter((f) => {
    const r = f?.rarity;
    const t = f?.tier;
    return !r || r === 'Unknown' || r === '-' || t == null || t === '-' || t === '';
  }).length;
}

function buildManualRarityProof(fishItems = []) {
  const overrides = loadOverrides();
  const rows = fishItems.slice(0, 30).map((f) => ({
    itemId: f.itemId || null,
    name: f.baseFishName || f.name || null,
    rarity: f.rarity || null,
    tier: f.tier || null,
    raritySource: f.raritySource || f.dataRaritySource || null,
  }));
  return {
    overridesPath: OVERRIDES_PATH,
    byNameCount: Object.keys(overrides.byName).length,
    byItemIdCount: Object.keys(overrides.byItemId).length,
    missingPublicRarityCount: countMissingPublicRarity(fishItems),
    rows,
  };
}

module.exports = {
  OVERRIDES_PATH,
  RARITY_TO_TIER,
  loadOverrides,
  resolvePublicFishRarity,
  countMissingPublicRarity,
  buildManualRarityProof,
  normalizeFishName,
};
