'use strict';

const manualRarity = require('./fishitManualRarityOverrides');
const stoneImageAssets = require('./fishitStoneImageAssets');
const GAMEITEMDB_ICON_SOURCE = 'gameitemdb_icon';
const PLAYERDATA_GAMEITEMDB_SOURCE = 'playerdata_gameitemdb';
const STONE_MANUAL_ASSET_SOURCE = stoneImageAssets.STONE_MANUAL_ASSET_SOURCE;
const QUIZ_BOT_FALLBACK_SOURCE = 'quiz_bot_fishit_bank';
const FINAL_BUILD = 'BLOCKER10ZI_INVENTORY_CARD_UNITY_2026_06_09';
const WAITING_ACTIVATION = 'waiting_for_playerdata_gameitemdb_payload';

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

function rowItemId(row) {
  if (!row || typeof row !== 'object') return '';
  const raw = row.itemId ?? row.ItemId ?? row.id ?? row.Id;
  return raw != null ? String(raw).trim() : '';
}

function rowSource(row) {
  if (!row || typeof row !== 'object') return null;
  return row.source || row.Source || null;
}

function isEnchantStoneRow(row) {
  const itemId = rowItemId(row);
  if (ENCHANT_STONE_IDS.has(itemId)) return true;
  const cat = String(row.category || row.Category || '').toLowerCase();
  const kind = String(row.kind || row.Kind || '').toLowerCase();
  const type = String(row.type || row.Type || '').toLowerCase();
  return cat === 'stone' || kind === 'stone' || type === 'enchantstone';
}

function isPlayerDataGameItemDbRow(item) {
  if (!item || typeof item !== 'object') return false;
  if (rowSource(item) === PLAYERDATA_GAMEITEMDB_SOURCE) return true;
  if (item.identityVerified === true && (isEnchantStoneRow(item) || item.kind === 'fish' || item.type === 'Fish')) {
    return true;
  }
  return false;
}

function detectGameItemDbUpload(body) {
  if (!body || typeof body !== 'object') return false;
  if (body.inventorySource === PLAYERDATA_GAMEITEMDB_SOURCE) return true;
  if (body.playerDataGameItemDbProof?.uploadPath === 'playerdata_gameitemdb') return true;
  if (body.sourceTruth?.identity === 'playerdata_itemutility_gameitemdb') return true;
  if (body.sourceTruth?.globalDbUsedForPublicIdentity === false
    && body.sourceTruth?.fishImage === GAMEITEMDB_ICON_SOURCE) {
    return true;
  }
  return false;
}

function expectsPlayerDataGameItemDbPayload(sessionData) {
  const build = String(sessionData?.trackerBuild || sessionData?.trackerClientProof?.trackerBuild || '');
  return /BLOCKER10Z[A-Z]|PLAYERDATA_GAMEITEMDB/i.test(build);
}

function defaultSourceTruth() {
  return {
    globalDbUsedForPublicIdentity: false,
    identity: 'playerdata_itemutility_gameitemdb',
    rarity: 'itemutility_tier',
    fishImage: GAMEITEMDB_ICON_SOURCE,
    stoneImage: GAMEITEMDB_ICON_SOURCE,
    fishIdentity: PLAYERDATA_GAMEITEMDB_SOURCE,
    fishRarity: 'playerdata_itemutility_tier',
    stoneIdentity: PLAYERDATA_GAMEITEMDB_SOURCE,
  };
}

function normaliseUploadRow(row) {
  if (!row || typeof row !== 'object') return null;
  const itemId = rowItemId(row);
  if (!itemId) return null;
  const quantity = Math.max(
    1,
    Math.floor(Number(row.quantity ?? row.Quantity ?? row.amount ?? row.count ?? 1)),
  );
  const iconRaw = row.icon ?? row.Icon ?? null;
  const iconParsed = parseGameItemIcon(iconRaw);
  const base = {
    itemId,
    quantity,
    amount: quantity,
    count: quantity,
    uuid: row.uuid || row.UUID || row.replionUuid || null,
    mutation: row.mutation || row.Mutation
      || (row.Metadata && row.Metadata.VariantId) || 'None',
    source: PLAYERDATA_GAMEITEMDB_SOURCE,
    identityVerified: true,
    icon: iconParsed?.icon || null,
    iconRaw,
    imageAssetId: iconParsed?.assetId || null,
    imageSource: iconParsed ? GAMEITEMDB_ICON_SOURCE : null,
  };
  if (isEnchantStoneRow(row)) {
    const stoneType = row.stoneType || row.StoneType || row.stone_type || null;
    const typeName = stoneType || 'Enchant';
    return {
      ...base,
      kind: 'stone',
      category: 'stone',
      stoneType: typeName,
      name: row.name || row.Name || `${typeName} Enchant Stone`,
      type: 'EnchantStone',
    };
  }
  const rowType = row.type || row.Type;
  const rowKind = row.kind || row.Kind;
  if (rowKind === 'fish' || rowType === 'Fish') {
    const name = String(row.baseName || row.base_name || row.name || row.Name || '').trim();
    if (!name || /^Unknown Fish #/i.test(name)) return null;
    const tier = row.tier != null ? row.tier : (row.Tier != null ? row.Tier : 1);
    const rarity = row.rarity || row.Rarity || tierToRarity(tier);
    return {
      ...base,
      kind: 'fish',
      category: 'fish',
      name,
      baseName: name,
      baseFishName: name,
      tier,
      rarity,
      type: 'Fish',
    };
  }
  return null;
}

function normaliseUploadRows(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.map(normaliseUploadRow).filter(Boolean);
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
    dataSource: PLAYERDATA_GAMEITEMDB_SOURCE,
  };
}

function mapToPublicFishCardItem(item) {
  const cleaned = applyPublicCosmetic(item);
  const amount = Number(cleaned.quantity) > 0 ? Math.floor(Number(cleaned.quantity)) : 1;
  const rarityResolved = manualRarity.resolvePublicFishRarity(cleaned, tierToRarity);
  const rarity = rarityResolved.rarity;
  const tier = rarityResolved.tier;
  const imageSource = cleaned.imageSource === GAMEITEMDB_ICON_SOURCE
    ? GAMEITEMDB_ICON_SOURCE
    : (cleaned.imageSource === QUIZ_BOT_FALLBACK_SOURCE
      ? QUIZ_BOT_FALLBACK_SOURCE
      : (cleaned.imageSource || null));
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
    tier,
    itemId: cleaned.itemId || null,
    category: 'fish',
    uuid: cleaned.uuid || null,
    mutation: null,
    mutationTags: [],
    debugMutation: cleaned.debugMutation || null,
    source: PLAYERDATA_GAMEITEMDB_SOURCE,
    dataSource: PLAYERDATA_GAMEITEMDB_SOURCE,
    identityVerified: true,
    identitySource: PLAYERDATA_GAMEITEMDB_SOURCE,
    globalDbUsedForPublicIdentity: false,
    raritySource: rarityResolved.raritySource,
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
    imageSource,
    dataImageSource: imageSource,
    dataRaritySource: rarityResolved.raritySource,
    icon: cleaned.iconRaw || cleaned.icon || null,
    debugIcon: cleaned.iconRaw || cleaned.icon || null,
    publicWeightHidden: true,
    publicRarityHidden: true,
  };
}

function mapToPublicStoneCardItem(item) {
  const amount = Number(item.quantity) > 0 ? Math.floor(Number(item.quantity)) : 1;
  const manualAsset = item.imageSource === STONE_MANUAL_ASSET_SOURCE && item.imageUrl;
  const iconParsed = manualAsset ? null : parseGameItemIcon(item.icon || item.iconRaw);
  const imageSource = manualAsset
    ? STONE_MANUAL_ASSET_SOURCE
    : (iconParsed
      ? GAMEITEMDB_ICON_SOURCE
      : (item.imageSource === QUIZ_BOT_FALLBACK_SOURCE ? QUIZ_BOT_FALLBACK_SOURCE : null));
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
    imageSource,
    dataSource: PLAYERDATA_GAMEITEMDB_SOURCE,
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
    (s) => (s.imageSource === GAMEITEMDB_ICON_SOURCE || s.imageSource === STONE_MANUAL_ASSET_SOURCE)
      && s.imageUrlPresent,
  ).length;
  return {
    enabled: true,
    build: extra.build || FINAL_BUILD,
    uploadPath: 'playerdata_gameitemdb',
    inventorySource: PLAYERDATA_GAMEITEMDB_SOURCE,
    gameItemDbBuilt: extra.gameItemDbBuilt !== false,
    gameItemDbCount: extra.gameItemDbCount != null ? extra.gameItemDbCount : null,
    gameItemDbTypeCounts: extra.gameItemDbTypeCounts || null,
    playerDataInventoryCount: extra.playerDataInventoryCount != null
      ? extra.playerDataInventoryCount
      : null,
    fishCount: fishItems.length,
    stoneCount: stoneItems.length,
    unresolvedCount: (unresolvedItems || []).length,
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
    rawFish: normaliseUploadRows(Array.isArray(rawFish) ? rawFish : []),
    rawStones: normaliseUploadRows(Array.isArray(rawStones) ? rawStones : []),
    unresolvedItems: Array.isArray(unresolved) ? unresolved : [],
  };
}

function usesPlayerDataGameItemDbPublicIdentity(sessionData) {
  if (!sessionData) return false;
  if (sessionData.inventorySource === PLAYERDATA_GAMEITEMDB_SOURCE) return true;
  if (sessionData.sourceTruth?.globalDbUsedForPublicIdentity === false
    && (sessionData.sourceTruth?.identity === 'playerdata_itemutility_gameitemdb'
      || sessionData.sourceTruth?.fishImage === GAMEITEMDB_ICON_SOURCE)) {
    return Boolean(
      (Array.isArray(sessionData.playerDataFishItems) && sessionData.playerDataFishItems.length)
      || (Array.isArray(sessionData.playerDataStoneItems) && sessionData.playerDataStoneItems.length)
      || sessionData.playerDataGameItemDbProof?.gameItemDbBuilt === true,
    );
  }
  return false;
}

function buildWaitingForPlayerDataGameItemDbResponse(sessionData = {}) {
  const storedProof = sessionData?.playerDataGameItemDbProof || {};
  return {
    activationState: WAITING_ACTIVATION,
    fishItems: [],
    stoneItems: [],
    publicItems: [],
    publicFishItems: [],
    fishInventory: buildInventoryGroups([]),
    stoneInventory: [],
    fishCounts: buildFishCounts([], [], 0),
    publicCounts: buildPublicCounts([], []),
    inventorySource: null,
    sourceTruth: sessionData?.sourceTruth || defaultSourceTruth(),
    playerDataGameItemDbProof: storedProof.enabled ? storedProof : null,
    playerDataItemUtilityProof: null,
    hiddenPublicRows: {
      ambiguousContainerUnresolved: 0,
      hiddenItemIds: [],
      reason: WAITING_ACTIVATION,
    },
    globalDbUiProof: null,
    trackerBuild: sessionData?.trackerBuild || null,
  };
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

  let stonesWithImages = stoneImageAssets.attachStoneImagesToItems(groupedStones, baseUrl);
  if (fishImageCache && typeof fishImageCache.attachItemUtilityGameIcons === 'function') {
    stonesWithImages = await fishImageCache.attachItemUtilityGameIcons(
      stonesWithImages.filter((s) => !s.imageUrlPresent),
      baseUrl,
    ).then((fallbackRows) => {
      const byKey = new Map(stonesWithImages.map((s) => [
        `${s.itemId}|${s.stoneType || ''}`,
        s,
      ]));
      for (const row of fallbackRows) {
        byKey.set(`${row.itemId}|${row.stoneType || ''}`, { ...byKey.get(`${row.itemId}|${row.stoneType || ''}`), ...row });
      }
      return [...byKey.values()];
    });
  }

  const fishItems = withImages.map((item) => mapToPublicFishCardItem(item));
  const stoneItems = stonesWithImages.map((item) => mapToPublicStoneCardItem(item));
  const missingPublicRarityCount = manualRarity.countMissingPublicRarity(fishItems);
  const manualRarityProof = manualRarity.buildManualRarityProof(fishItems);
  const stoneAssetProof = stoneImageAssets.buildStoneAssetProof(stoneItems);
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
    activationState: 'playerdata_gameitemdb_active',
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
    missingPublicRarityCount,
    manualRarityProof,
    stoneAssetProof,
  };
}

module.exports = {
  FINAL_BUILD,
  WAITING_ACTIVATION,
  PLAYERDATA_GAMEITEMDB_SOURCE,
  GAMEITEMDB_ICON_SOURCE,
  QUIZ_BOT_FALLBACK_SOURCE,
  TIER_NAMES,
  ENCHANT_STONE_IDS,
  tierToRarity,
  parseGameItemIcon,
  isValidPublicGameIcon,
  isPlayerDataGameItemDbRow,
  isEnchantStoneRow,
  detectGameItemDbUpload,
  expectsPlayerDataGameItemDbPayload,
  normaliseUploadRow,
  normaliseUploadRows,
  groupFishRows,
  groupStoneRows,
  buildPublicFromPlayerDataGameItemDb,
  buildWaitingForPlayerDataGameItemDbResponse,
  buildPlayerDataGameItemDbProof,
  usesPlayerDataGameItemDbPublicIdentity,
  defaultSourceTruth,
  extractSessionRows,
  mapToPublicFishCardItem,
  mapToPublicStoneCardItem,
};
