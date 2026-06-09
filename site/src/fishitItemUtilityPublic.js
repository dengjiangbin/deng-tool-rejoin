'use strict';

const PLAYERDATA_ITEMUTILITY_SOURCE = 'playerdata_itemutility';

const ENCHANT_STONE_IDS = new Set(['10', '246', '558', '873', '929']);

function isPlayerDataItemUtilityRow(item) {
  return Boolean(
    item
    && item.source === PLAYERDATA_ITEMUTILITY_SOURCE
    && item.identityVerified === true,
  );
}

function normaliseUploadRow(row) {
  if (!row || typeof row !== 'object') return null;
  const itemId = row.itemId != null ? String(row.itemId).trim() : '';
  const quantity = Math.max(
    1,
    Math.floor(Number(row.quantity ?? row.amount ?? row.count ?? 1)),
  );
  const base = {
    itemId,
    quantity,
    amount: quantity,
    count: quantity,
    uuid: row.uuid || row.replionUuid || null,
    mutation: row.mutation || 'None',
    source: PLAYERDATA_ITEMUTILITY_SOURCE,
    identityVerified: true,
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
    if (!name) return null;
    return {
      ...base,
      kind: 'fish',
      category: 'fish',
      name,
      baseName: name,
      baseFishName: name,
      tier: row.tier || row.rarity || null,
      rarity: row.tier || row.rarity || null,
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

function applyItemUtilityPublicCosmetic(item) {
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
    identitySource: PLAYERDATA_ITEMUTILITY_SOURCE,
    globalDbUsedForPublicIdentity: false,
  };
}

function mapToPublicFishCardItem(item) {
  const cleaned = applyItemUtilityPublicCosmetic(item);
  const amount = Number(cleaned.quantity) > 0 ? Math.floor(Number(cleaned.quantity)) : 1;
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
    rarity: cleaned.rarity && cleaned.rarity !== 'Unknown' ? cleaned.rarity : null,
    tier: cleaned.tier || null,
    itemId: cleaned.itemId || null,
    category: 'fish',
    uuid: cleaned.uuid || null,
    mutation: null,
    mutationTags: [],
    debugMutation: cleaned.debugMutation || null,
    source: PLAYERDATA_ITEMUTILITY_SOURCE,
    identityVerified: true,
    identitySource: PLAYERDATA_ITEMUTILITY_SOURCE,
    globalDbUsedForPublicIdentity: false,
    publicIdentityProof: {
      identitySource: PLAYERDATA_ITEMUTILITY_SOURCE,
      globalDbUsedForPublicIdentity: false,
      itemId: cleaned.itemId || null,
      itemUtilityName: cleaned.baseFishName,
      mutation: cleaned.debugMutation || null,
    },
    imageUrl: cleaned.imageUrl || null,
    imageUrlPresent: Boolean(cleaned.imageUrl),
    imageSource: cleaned.imageSource || null,
    publicWeightHidden: true,
  };
}

function mapToPublicStoneCardItem(item) {
  const amount = Number(item.quantity) > 0 ? Math.floor(Number(item.quantity)) : 1;
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
    source: PLAYERDATA_ITEMUTILITY_SOURCE,
    identityVerified: true,
    identitySource: PLAYERDATA_ITEMUTILITY_SOURCE,
    globalDbUsedForPublicIdentity: false,
  };
}

function buildPlayerDataItemUtilityProof(fishItems, stoneItems, hiddenUnresolvedRows = []) {
  return {
    enabled: true,
    source: PLAYERDATA_ITEMUTILITY_SOURCE,
    itemUtilityResolvedFishCount: fishItems.length,
    itemUtilityResolvedStoneCount: stoneItems.length,
    itemUtilityResolvedFishInstances: fishItems.reduce(
      (s, f) => s + (Number(f.amount) > 0 ? Math.floor(Number(f.amount)) : 1),
      0,
    ),
    itemUtilityResolvedStoneInstances: stoneItems.reduce(
      (s, st) => s + (Number(st.amount) > 0 ? Math.floor(Number(st.amount)) : 1),
      0,
    ),
    globalDbUsedForPublicIdentity: false,
    sampleFish: fishItems.slice(0, 5).map((f) => ({
      itemId: f.itemId,
      name: f.baseFishName || f.name,
      amount: f.amount,
      mutation: f.debugMutation || null,
    })),
    sampleStones: stoneItems.slice(0, 5).map((s) => ({
      itemId: s.itemId,
      stoneType: s.stoneType,
      name: s.name,
      amount: s.amount,
    })),
    hiddenUnresolvedRows: (hiddenUnresolvedRows || []).slice(0, 20),
  };
}

function buildInventoryGroups(fishItems) {
  return { fish: fishItems, rods: [], items: [], stones: [] };
}

function buildStoneInventoryGroups(stoneItems) {
  return stoneItems;
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

function defaultSourceTruth() {
  return {
    fishIdentity: PLAYERDATA_ITEMUTILITY_SOURCE,
    stoneIdentity: PLAYERDATA_ITEMUTILITY_SOURCE,
    globalDbUsedForPublicIdentity: false,
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
  const hidden = sessionData?.playerDataHiddenUnresolved
    || body?.hiddenUnresolvedRows
    || [];
  return {
    rawFish: Array.isArray(rawFish) ? rawFish.filter(isPlayerDataItemUtilityRow) : [],
    rawStones: Array.isArray(rawStones) ? rawStones.filter(isPlayerDataItemUtilityRow) : [],
    hiddenUnresolvedRows: Array.isArray(hidden) ? hidden : [],
  };
}

async function buildPublicFromPlayerDataItemUtility(sessionData, baseUrl, deps = {}) {
  const fishImageAssets = deps.fishImageAssets;
  const rarityEnrichment = deps.rarityEnrichment;
  const fishImageCache = deps.fishImageCache;
  const { rawFish, rawStones, hiddenUnresolvedRows } = extractSessionRows(sessionData);
  const groupedFish = groupFishRows(rawFish);
  const groupedStones = groupStoneRows(rawStones);

  let withAssets = groupedFish;
  if (fishImageAssets && typeof fishImageAssets.attachFishImagesToItems === 'function') {
    withAssets = fishImageAssets.attachFishImagesToItems(groupedFish);
  }
  let withRarity = withAssets;
  if (rarityEnrichment && typeof rarityEnrichment.attachRarityToItems === 'function') {
    withRarity = rarityEnrichment.attachRarityToItems(withAssets);
  }
  let withImages = withRarity;
  if (fishImageCache && typeof fishImageCache.attachCachedImagesToItems === 'function') {
    withImages = await fishImageCache.attachCachedImagesToItems(withRarity, baseUrl);
  }

  const fishItems = withImages.map((item) => mapToPublicFishCardItem(item));
  const stoneItems = groupedStones.map((item) => mapToPublicStoneCardItem(item));
  const fishCounts = buildFishCounts(fishItems, stoneItems, hiddenUnresolvedRows.length);
  const publicCounts = buildPublicCounts(fishItems, stoneItems);
  const playerDataItemUtilityProof = buildPlayerDataItemUtilityProof(
    fishItems,
    stoneItems,
    hiddenUnresolvedRows,
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
    inventorySource: PLAYERDATA_ITEMUTILITY_SOURCE,
    sourceTruth: sessionData?.sourceTruth || defaultSourceTruth(),
    playerDataItemUtilityProof,
    hiddenPublicRows: {
      ambiguousContainerUnresolved: 0,
      hiddenItemIds: hiddenUnresolvedRows.map((r) => r.itemId).filter(Boolean),
      reason: 'itemutility_unresolved',
    },
    globalDbUiProof: null,
  };
}

function usesPlayerDataItemUtilityPublicIdentity(sessionData) {
  return sessionData?.inventorySource === PLAYERDATA_ITEMUTILITY_SOURCE
    || sessionData?.sourceTruth?.globalDbUsedForPublicIdentity === false
      && sessionData?.sourceTruth?.fishIdentity === PLAYERDATA_ITEMUTILITY_SOURCE;
}

module.exports = {
  PLAYERDATA_ITEMUTILITY_SOURCE,
  ENCHANT_STONE_IDS,
  isPlayerDataItemUtilityRow,
  normaliseUploadRow,
  groupFishRows,
  groupStoneRows,
  buildPublicFromPlayerDataItemUtility,
  buildPlayerDataItemUtilityProof,
  usesPlayerDataItemUtilityPublicIdentity,
  defaultSourceTruth,
  extractSessionRows,
  mapToPublicFishCardItem,
  mapToPublicStoneCardItem,
};
