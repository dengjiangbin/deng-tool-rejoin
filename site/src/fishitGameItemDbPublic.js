'use strict';

const PLAYERDATA_GAMEITEMDB_SOURCE = 'playerdata_gameitemdb';
const GAMEITEMDB_ICON_SOURCE = 'gameitemdb_icon';
const FINAL_BUILD = 'BLOCKER10ZA_FINAL_PLAYERDATA_GAMEITEMDB_UPLOAD_2026_06_09';

const ENCHANT_STONE_IDS = new Set(['10', '246', '558', '873', '929']);

const TIER_NAMES = {
  1: 'Common',
  2: 'Uncommon',
  3: 'Rare',
  4: 'Epic',
  5: 'Legendary',
  6: 'Mythic',
  7: 'Secret',
  8: 'Forgotten',
};

function tierToRarity(tier) {
  const n = Number(tier);
  if (Number.isFinite(n) && TIER_NAMES[n]) return TIER_NAMES[n];
  if (typeof tier === 'string' && tier.trim()) return tier.trim();
  return 'Unknown';
}

function parseGameItemIcon(raw) {
  if (raw == null || raw === '') return null;
  if (typeof raw === 'number') {
    if (raw <= 0) return null;
    return {
      icon: `rbxassetid://${raw}`,
      assetId: String(raw),
      imageSource: GAMEITEMDB_ICON_SOURCE,
    };
  }
  const s = String(raw).trim();
  if (!s || s === '0' || s.toLowerCase() === 'rbxassetid://0') return null;
  const prefixed = s.match(/^rbxassetid:\/\/(\d+)$/i);
  if (prefixed) {
    if (prefixed[1] === '0') return null;
    return {
      icon: s,
      assetId: prefixed[1],
      imageSource: GAMEITEMDB_ICON_SOURCE,
    };
  }
  if (/^\d+$/.test(s)) {
    if (s === '0') return null;
    return {
      icon: `rbxassetid://${s}`,
      assetId: s,
      imageSource: GAMEITEMDB_ICON_SOURCE,
    };
  }
  return null;
}

function isValidPublicGameIcon(parsed) {
  return Boolean(parsed?.assetId && parsed.assetId !== '0');
}

function isPlayerDataGameItemDbRow(item) {
  return Boolean(
    item
    && item.source === PLAYERDATA_GAMEITEMDB_SOURCE
    && item.identityVerified === true,
  );
}

function defaultSourceTruth() {
  return {
    fishIdentity: PLAYERDATA_GAMEITEMDB_SOURCE,
    fishRarity: 'playerdata_itemutility_tier',
    fishImage: GAMEITEMDB_ICON_SOURCE,
    stoneIdentity: PLAYERDATA_GAMEITEMDB_SOURCE,
    globalDbUsedForPublicIdentity: false,
  };
}

function normaliseUploadRow(row) {
  if (!row || typeof row !== 'object') return null;
  const itemId = row.itemId != null ? String(row.itemId).trim() : '';
  const quantity = Math.max(
    1,
    Math.floor(Number(row.quantity ?? row.amount ?? row.count ?? 1)),
  );
  const iconParsed = parseGameItemIcon(row.icon);
  const base = {
    itemId,
    quantity,
    amount: quantity,
    count: quantity,
    uuid: row.uuid || row.replionUuid || null,
    mutation: row.mutation || 'None',
    source: PLAYERDATA_GAMEITEMDB_SOURCE,
    identityVerified: true,
    icon: iconParsed?.icon || null,
    iconRaw: row.icon || null,
    imageAssetId: iconParsed?.assetId || null,
    imageSource: iconParsed ? GAMEITEMDB_ICON_SOURCE : null,
  };
  if (row.kind === 'stone' || ENCHANT_STONE_IDS.has(itemId)) {
    const stoneType = row.stoneType || row.stone_type || null;
    return {
      ...base,
      kind: 'stone',
      category: 'stone',
      stoneType,
      name: row.name || (stoneType ? `${stoneType} Enchant Stone` : 'Enchant Stone'),
    };
  }
  if (row.kind === 'fish' || row.type === 'Fish') {
    const name = String(row.baseName || row.name || '').trim();
    if (!name || /^Unknown Fish #/i.test(name)) return null;
    const tier = row.tier != null ? row.tier : 1;
    const rarity = row.rarity || tierToRarity(tier);
    return {
      ...base,
      kind: 'fish',
      category: 'fish',
      name,
      baseName: name,
      baseFishName: name,
      tier,
      rarity,
      type: row.type || 'Fish',
    };
  }
  return null;
}

function groupFishRows(rows) {
  const map = new Map();
  for (const row of rows) {
    const norm = normaliseUploadRow(row);
    if (!norm || norm.kind !== 'fish') continue;
    const key = `${norm.itemId}|${norm.baseName}`;
    const prev = map.get(key);
    if (prev) {
      prev.quantity += norm.quantity;
      prev.amount = prev.quantity;
      prev.count = prev.quantity;
    } else {
      map.set(key, { ...norm });
    }
  }
  return [...map.values()];
}

function groupStoneRows(rows) {
  const map = new Map();
  for (const row of rows) {
    const norm = normaliseUploadRow(row);
    if (!norm || norm.kind !== 'stone') continue;
    const key = norm.stoneType || norm.itemId;
    const prev = map.get(key);
    if (prev) {
      prev.quantity += norm.quantity;
      prev.amount = prev.quantity;
      prev.count = prev.quantity;
    } else {
      map.set(key, { ...norm });
    }
  }
  return [...map.values()];
}

function applyPublicCosmetic(item) {
  const baseName = String(item.baseName || item.baseFishName || item.name || '').trim();
  return {
    ...item,
    cardName: baseName,
    name: baseName,
    displayName: baseName,
    baseFishName: baseName,
    publicCardName: baseName,
    debugMutation: item.mutation && item.mutation !== 'None' ? item.mutation : null,
    mutation: null,
    mutationTags: [],
    publicMutationHidden: true,
    identitySource: PLAYERDATA_GAMEITEMDB_SOURCE,
    globalDbUsedForPublicIdentity: false,
    raritySource: 'playerdata_itemutility_tier',
  };
}

function mapToPublicFishCardItem(item) {
  const cleaned = applyPublicCosmetic(item);
  const amount = Number(cleaned.quantity) > 0 ? Math.floor(Number(cleaned.quantity)) : 1;
  const rarity = cleaned.rarity && cleaned.rarity !== 'Unknown' ? cleaned.rarity : null;
  return {
    speciesId: cleaned.itemId || null,
    canonicalName: cleaned.baseFishName,
    displayName: cleaned.baseFishName,
    name: cleaned.baseFishName,
    cardName: cleaned.baseFishName,
    publicCardName: cleaned.baseFishName,
    baseFishName: cleaned.baseFishName,
    amount,
    quantity: amount,
    count: amount,
    rarity,
    tier: cleaned.tier || null,
    itemId: cleaned.itemId || null,
    category: 'fish',
    uuid: cleaned.uuid || null,
    mutation: null,
    mutationTags: [],
    debugMutation: cleaned.debugMutation || null,
    source: PLAYERDATA_GAMEITEMDB_SOURCE,
    identityVerified: true,
    identitySource: PLAYERDATA_GAMEITEMDB_SOURCE,
    globalDbUsedForPublicIdentity: false,
    publicIdentityProof: {
      identitySource: PLAYERDATA_GAMEITEMDB_SOURCE,
      globalDbUsedForPublicIdentity: false,
      itemId: cleaned.itemId || null,
      itemUtilityName: cleaned.baseFishName,
      tier: cleaned.tier || null,
      rarity,
      mutation: cleaned.debugMutation || null,
    },
    imageUrl: cleaned.imageUrl || null,
    imageUrlPresent: Boolean(cleaned.imageUrl),
    imageSource: cleaned.imageSource || null,
    icon: cleaned.iconRaw || cleaned.icon || null,
    debugIcon: cleaned.iconRaw || cleaned.icon || null,
    publicWeightHidden: true,
    publicRarityHidden: true,
  };
}

function mapToPublicStoneCardItem(item) {
  const amount = Number(item.quantity) > 0 ? Math.floor(Number(item.quantity)) : 1;
  const iconParsed = parseGameItemIcon(item.icon || item.iconRaw);
  return {
    kind: 'stone',
    category: 'stone',
    itemId: item.itemId || null,
    stoneType: item.stoneType || null,
    name: item.name,
    displayName: item.name,
    amount,
    quantity: amount,
    count: amount,
    uuid: item.uuid || null,
    mutation: item.mutation && item.mutation !== 'None' ? item.mutation : null,
    icon: iconParsed?.icon || item.icon || null,
    imageUrl: item.imageUrl || null,
    imageUrlPresent: Boolean(item.imageUrl),
    imageSource: iconParsed ? GAMEITEMDB_ICON_SOURCE : (item.imageSource || null),
    source: PLAYERDATA_GAMEITEMDB_SOURCE,
    identityVerified: true,
    identitySource: PLAYERDATA_GAMEITEMDB_SOURCE,
    globalDbUsedForPublicIdentity: false,
  };
}

function buildPlayerDataGameItemDbProof(fishItems, stoneItems, unresolvedItems = [], extra = {}) {
  const fishIconResolvedCount = fishItems.filter(
    (f) => f.imageSource === GAMEITEMDB_ICON_SOURCE && f.imageUrlPresent,
  ).length;
  const stoneIconResolvedCount = stoneItems.filter(
    (s) => s.imageSource === GAMEITEMDB_ICON_SOURCE && s.imageUrlPresent,
  ).length;
  return {
    enabled: true,
    build: FINAL_BUILD,
    inventorySource: PLAYERDATA_GAMEITEMDB_SOURCE,
    gameItemDbBuilt: extra.gameItemDbBuilt !== false,
    gameItemDbCount: extra.gameItemDbCount != null ? extra.gameItemDbCount : null,
    itemUtilityResolvedFishCount: extra.itemUtilityResolvedFishCount != null
      ? extra.itemUtilityResolvedFishCount
      : fishItems.length,
    uploadedFishCount: fishItems.length,
    uploadedStoneCount: stoneItems.length,
    fishIconResolvedCount: extra.fishIconResolvedCount != null
      ? extra.fishIconResolvedCount
      : fishIconResolvedCount,
    stoneIconResolvedCount: extra.stoneIconResolvedCount != null
      ? extra.stoneIconResolvedCount
      : stoneIconResolvedCount,
    globalDbUsedForPublicIdentity: false,
    sampleFish: fishItems.slice(0, 5).map((f) => ({
      itemId: f.itemId,
      name: f.baseFishName || f.name,
      quantity: f.amount,
      tier: f.tier,
      rarity: f.rarity,
      icon: f.debugIcon || f.icon || null,
      source: PLAYERDATA_GAMEITEMDB_SOURCE,
    })),
    sampleStones: stoneItems.slice(0, 5).map((s) => ({
      itemId: s.itemId,
      name: s.name,
      stoneType: s.stoneType,
      quantity: s.amount,
      icon: s.icon || null,
      source: PLAYERDATA_GAMEITEMDB_SOURCE,
    })),
    unresolvedItems: (unresolvedItems || []).slice(0, 20),
  };
}

function buildInventoryGroups(fishItems) {
  return { fish: fishItems, rods: [], items: [], stones: [] };
}

function buildPublicCounts(fishItems, stoneItems) {
  const visibleFishInstances = fishItems.reduce(
    (s, f) => s + (Number(f.amount) > 0 ? Math.floor(Number(f.amount)) : 1),
    0,
  );
  const visibleStoneInstances = stoneItems.reduce(
    (s, st) => s + (Number(st.amount) > 0 ? Math.floor(Number(st.amount)) : 1),
    0,
  );
  return {
    visibleFishInstances,
    visibleFishTypes: fishItems.length,
    visibleStoneInstances,
    visibleStoneTypes: stoneItems.length,
    hiddenUnresolvedFishRows: 0,
    hiddenAmbiguousContainerRows: 0,
  };
}

function buildFishCounts(fishItems, stoneItems, hiddenUnresolved = 0) {
  const fishInstances = fishItems.reduce(
    (s, f) => s + (Number(f.amount) > 0 ? Math.floor(Number(f.amount)) : 1),
    0,
  );
  const stoneInstances = stoneItems.reduce(
    (s, st) => s + (Number(st.amount) > 0 ? Math.floor(Number(st.amount)) : 1),
    0,
  );
  return {
    label: 'Fish',
    fishTypes: fishItems.length,
    fishInstances,
    stoneTypes: stoneItems.length,
    stoneInstances,
    hiddenUnresolvedFishRows: hiddenUnresolved,
    hiddenNonFishTypes: 0,
    hiddenNonFishInstances: 0,
  };
}

function extractSessionRows(sessionData, body = null) {
  const rawFish = sessionData?.playerDataFishItems
    || sessionData?.fishItemsRaw
    || body?.fishItems
    || [];
  const rawStones = sessionData?.playerDataStoneItems
    || sessionData?.stoneItemsRaw
    || body?.stoneItems
    || [];
  const unresolved = sessionData?.playerDataUnresolvedItems
    || sessionData?.unresolvedItems
    || body?.unresolvedItems
    || [];
  return {
    rawFish: Array.isArray(rawFish) ? rawFish.filter(isPlayerDataGameItemDbRow) : [],
    rawStones: Array.isArray(rawStones) ? rawStones.filter(isPlayerDataGameItemDbRow) : [],
    unresolvedItems: Array.isArray(unresolved) ? unresolved : [],
  };
}

function usesPlayerDataGameItemDbPublicIdentity(sessionData) {
  return sessionData?.inventorySource === PLAYERDATA_GAMEITEMDB_SOURCE
    || (sessionData?.sourceTruth?.globalDbUsedForPublicIdentity === false
      && sessionData?.sourceTruth?.fishIdentity === PLAYERDATA_GAMEITEMDB_SOURCE);
}

async function buildPublicFromPlayerDataGameItemDb(sessionData, baseUrl, deps = {}) {
  const fishImageCache = deps.fishImageCache;
  const { rawFish, rawStones, unresolvedItems } = extractSessionRows(sessionData);
  const groupedFish = groupFishRows(rawFish);
  const groupedStones = groupStoneRows(rawStones);

  let withImages = groupedFish;
  if (fishImageCache && typeof fishImageCache.attachItemUtilityGameIcons === 'function') {
    withImages = await fishImageCache.attachItemUtilityGameIcons(groupedFish, baseUrl);
  } else if (fishImageCache && typeof fishImageCache.attachCachedImagesToItems === 'function') {
    withImages = await fishImageCache.attachCachedImagesToItems(groupedFish, baseUrl);
  }

  let stonesWithImages = groupedStones;
  if (fishImageCache && typeof fishImageCache.attachItemUtilityGameIcons === 'function') {
    stonesWithImages = await fishImageCache.attachItemUtilityGameIcons(groupedStones, baseUrl);
  }

  const fishItems = withImages.map((item) => mapToPublicFishCardItem(item));
  const stoneItems = stonesWithImages.map((item) => mapToPublicStoneCardItem(item));
  const fishCounts = buildFishCounts(fishItems, stoneItems, unresolvedItems.length);
  const publicCounts = buildPublicCounts(fishItems, stoneItems);
  const storedProof = sessionData?.playerDataGameItemDbProof || {};
  const playerDataGameItemDbProof = buildPlayerDataGameItemDbProof(
    fishItems,
    stoneItems,
    unresolvedItems,
    storedProof,
  );

  return {
    fishItems,
    stoneItems,
    publicItems: fishItems,
    publicFishItems: fishItems,
    fishInventory: buildInventoryGroups(fishItems),
    stoneInventory: stoneItems,
    fishCounts,
    publicCounts,
    inventorySource: PLAYERDATA_GAMEITEMDB_SOURCE,
    sourceTruth: sessionData?.sourceTruth || defaultSourceTruth(),
    playerDataGameItemDbProof,
    playerDataItemUtilityProof: null,
    hiddenPublicRows: {
      ambiguousContainerUnresolved: 0,
      hiddenItemIds: unresolvedItems.map((r) => r.itemId).filter(Boolean),
      reason: 'gameitemdb_unresolved',
    },
    globalDbUiProof: null,
  };
}

module.exports = {
  FINAL_BUILD,
  PLAYERDATA_GAMEITEMDB_SOURCE,
  GAMEITEMDB_ICON_SOURCE,
  TIER_NAMES,
  ENCHANT_STONE_IDS,
  tierToRarity,
  parseGameItemIcon,
  isValidPublicGameIcon,
  isPlayerDataGameItemDbRow,
  normaliseUploadRow,
  groupFishRows,
  groupStoneRows,
  buildPublicFromPlayerDataGameItemDb,
  buildPlayerDataGameItemDbProof,
  usesPlayerDataGameItemDbPublicIdentity,
  defaultSourceTruth,
  extractSessionRows,
  mapToPublicFishCardItem,
  mapToPublicStoneCardItem,
};
