'use strict';

const RARITY_ORDER = {
  Forgotten: 800,
  Secret: 700,
  Mythic: 600,
  Legendary: 500,
  Epic: 400,
  Rare: 300,
  Uncommon: 200,
  Common: 100,
  Unknown: 0,
};

const TIER_TO_RARITY = {
  1: 'Common',
  2: 'Uncommon',
  3: 'Rare',
  4: 'Epic',
  5: 'Legendary',
  6: 'Mythic',
  7: 'Secret',
  8: 'Forgotten',
};

const STONE_TYPE_ORDER = {
  Normal: 10,
  Double: 20,
  Evolved: 30,
  Eggy: 40,
  Runic: 50,
};

function normalizeRarityLabel(item) {
  if (!item || typeof item !== 'object') return 'Unknown';
  const raw = item.rarity ?? item.Rarity;
  if (raw != null && String(raw).trim() && String(raw).trim() !== 'Unknown') {
    return String(raw).trim();
  }
  const tier = Number(item.tier ?? item.Tier);
  if (Number.isFinite(tier) && TIER_TO_RARITY[tier]) {
    return TIER_TO_RARITY[tier];
  }
  if (typeof raw === 'string' && raw.trim()) return raw.trim();
  return 'Unknown';
}

function rarityRank(item) {
  const rarity = normalizeRarityLabel(item);
  return RARITY_ORDER[rarity] ?? RARITY_ORDER.Unknown;
}

function itemDisplayName(item) {
  return String(item?.name || item?.Name || item?.baseFishName || item?.displayName || '').trim();
}

function itemStableId(item) {
  return String(item?.itemId ?? item?.ItemId ?? item?.speciesId ?? '').trim();
}

function sortInventoryFish(items) {
  if (!Array.isArray(items)) return [];
  return [...items].sort((a, b) => {
    const rarityDiff = rarityRank(b) - rarityRank(a);
    if (rarityDiff) return rarityDiff;

    const nameA = itemDisplayName(a).toLowerCase();
    const nameB = itemDisplayName(b).toLowerCase();
    if (nameA !== nameB) return nameA.localeCompare(nameB);

    return itemStableId(a).localeCompare(itemStableId(b));
  });
}

function stoneTypeRank(item) {
  const type = String(item?.stoneType || item?.StoneType || '').trim();
  if (type && STONE_TYPE_ORDER[type] != null) return STONE_TYPE_ORDER[type];
  const itemId = itemStableId(item);
  const byId = { 10: 10, 246: 20, 558: 30, 873: 40, 929: 50 };
  if (itemId && byId[itemId] != null) return byId[itemId];
  return 999;
}

function sortInventoryStones(items) {
  if (!Array.isArray(items)) return [];
  return [...items].sort((a, b) => {
    const typeDiff = stoneTypeRank(a) - stoneTypeRank(b);
    if (typeDiff) return typeDiff;
    return itemStableId(a).localeCompare(itemStableId(b));
  });
}

function applyPublicInventorySort(result) {
  if (!result || typeof result !== 'object') return result;
  const sortedFish = sortInventoryFish(result.fishItems || []);
  const sortedStones = sortInventoryStones(result.stoneItems || result.stoneInventory || []);
  const fishInventory = result.fishInventory && typeof result.fishInventory === 'object'
    ? { ...result.fishInventory, fish: sortedFish }
    : result.fishInventory;

  return {
    ...result,
    fishItems: sortedFish,
    publicItems: sortedFish,
    publicFishItems: sortedFish,
    stoneItems: sortedStones,
    stoneInventory: sortedStones,
    fishInventory,
    inventorySortProof: {
      fishOrder: sortedFish.slice(0, 15).map((f) => ({
        name: itemDisplayName(f),
        rarity: normalizeRarityLabel(f),
        rank: rarityRank(f),
        itemId: itemStableId(f) || null,
      })),
      stoneOrder: sortedStones.slice(0, 10).map((s) => ({
        name: itemDisplayName(s),
        stoneType: s.stoneType || null,
        itemId: itemStableId(s) || null,
      })),
    },
  };
}

module.exports = {
  RARITY_ORDER,
  STONE_TYPE_ORDER,
  normalizeRarityLabel,
  rarityRank,
  itemDisplayName,
  itemStableId,
  sortInventoryFish,
  sortInventoryStones,
  applyPublicInventorySort,
};
