'use strict';

const {
  normalizeRarityLabel,
  rarityRank,
  sortInventoryFish,
  sortInventoryStones,
  itemDisplayName,
  itemStableId,
} = require('./fishitInventorySort');

const FALLBACK_HINTS = [/fallback/i, /placeholder/i];

function resolveItemAmount(item) {
  if (!item || typeof item !== 'object') return 0;
  const raw = item.amount ?? item.quantity ?? item.Quantity ?? 0;
  const n = Number(raw);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.floor(n));
}

function canonicalBulkName(item) {
  return String(item?.baseFishName || item?.displayName || item?.name || item?.Name || '').trim()
    || itemDisplayName(item);
}

function bulkGroupKey(category, item) {
  const cat = String(category || 'fish').toLowerCase();
  const name = canonicalBulkName(item);
  if (cat === 'stone') {
    const stoneType = String(item?.stoneType || item?.StoneType || '').trim();
    const rarity = normalizeRarityLabel(item);
    return `${cat}:${name.toLowerCase()}:${stoneType.toLowerCase() || rarity.toLowerCase()}`;
  }
  if (cat === 'totem') {
    // Group by canonical totem identity (name + type itemId). Never key on the
    // per-instance uuid — that produced a duplicate totem card per username.
    const stable = String(item?.itemId || item?.itemID || '').trim().toLowerCase();
    return stable ? `${cat}:${name.toLowerCase()}:${stable}` : `${cat}:${name.toLowerCase()}`;
  }
  const rarity = normalizeRarityLabel(item);
  return `${cat}:${name.toLowerCase()}:${rarity.toLowerCase()}`;
}

function isPlaceholderImageUrl(url) {
  const u = String(url || '');
  return !u || FALLBACK_HINTS.some((re) => re.test(u));
}

function pickImageUrl(existing, candidate) {
  if (!isPlaceholderImageUrl(candidate)) return candidate;
  if (!isPlaceholderImageUrl(existing)) return existing;
  return existing || candidate || null;
}

function mergeBulkItem(existing, item, username, category) {
  const amount = resolveItemAmount(item);
  const imageUrl = pickImageUrl(existing.imageUrl, item.imageUrl || item.image || null);
  // Track a representative quantity PER distinct owner. A username that re-sends
  // the same backpack item (duplicate rows) must not inflate the total or the
  // contributor count — we keep the largest amount seen for that owner and sum
  // once per owner. Different usernames each contribute their own amount.
  const ownerAmounts = Object.assign({}, existing.ownerAmounts);
  const ownerKey = String(username || '').trim().toLowerCase();
  if (ownerKey) {
    ownerAmounts[ownerKey] = Math.max(Number(ownerAmounts[ownerKey]) || 0, amount);
  }
  const owners = Object.keys(ownerAmounts);
  const totalAmount = owners.reduce((sum, k) => sum + (Number(ownerAmounts[k]) || 0), 0);
  return {
    ...existing,
    name: existing.name || canonicalBulkName(item),
    category: category || existing.category,
    rarity: existing.rarity || normalizeRarityLabel(item),
    stoneType: existing.stoneType || item.stoneType || item.StoneType || null,
    itemId: existing.itemId || itemStableId(item) || null,
    imageUrl,
    imageAssetId: existing.imageAssetId || item.imageAssetId || null,
    amount: totalAmount,
    accountCount: owners.length,
    owners,
    ownerAmounts,
    dataSource: 'bulk_playerdata_gameitemdb',
    groupKey: existing.groupKey,
  };
}

function aggregateBulkInventory(sessions) {
  const fishMap = new Map();
  const stoneMap = new Map();
  const totemMap = new Map();
  const accountSet = new Set();

  for (const session of sessions || []) {
    const username = String(session?.username || session?.displayName || '').trim();
    if (username) accountSet.add(username.toLowerCase());
    for (const item of session?.fishList || []) {
      const key = bulkGroupKey('fish', item);
      const prev = fishMap.get(key);
      fishMap.set(key, mergeBulkItem(prev || {
        groupKey: key,
        name: canonicalBulkName(item),
        category: 'fish',
        rarity: normalizeRarityLabel(item),
        amount: 0,
        accountCount: 0,
        owners: [],
        imageUrl: null,
      }, item, username, 'fish'));
    }
    for (const item of session?.stoneList || []) {
      const key = bulkGroupKey('stone', item);
      const prev = stoneMap.get(key);
      stoneMap.set(key, mergeBulkItem(prev || {
        groupKey: key,
        name: canonicalBulkName(item),
        category: 'stone',
        rarity: normalizeRarityLabel(item),
        stoneType: item.stoneType || item.StoneType || null,
        amount: 0,
        accountCount: 0,
        owners: [],
        imageUrl: null,
      }, item, username, 'stone'));
    }
    for (const item of session?.totemList || []) {
      const key = bulkGroupKey('totem', item);
      const prev = totemMap.get(key);
      totemMap.set(key, mergeBulkItem(prev || {
        groupKey: key,
        name: canonicalBulkName(item),
        category: 'totem',
        rarity: normalizeRarityLabel(item),
        amount: 0,
        accountCount: 0,
        owners: [],
        imageUrl: null,
      }, item, username, 'totem'));
    }
  }

  return {
    fish: sortInventoryFish([...fishMap.values()]),
    stones: sortInventoryStones([...stoneMap.values()]),
    totems: [...totemMap.values()],
    accountCount: accountSet.size,
    fishTypeCount: fishMap.size,
    stoneTypeCount: stoneMap.size,
    totemTypeCount: totemMap.size,
  };
}

function bulkSearchHaystack(item) {
  if (!item || typeof item !== 'object') return '';
  return [
    item.name,
    item.rarity,
    item.stoneType,
    item.itemId,
    item.groupKey,
    ...(Array.isArray(item.owners) ? item.owners : []),
    String(item.amount || ''),
  ].filter(Boolean).join(' ').toLowerCase();
}

function filterBulkItems(items, query) {
  if (!Array.isArray(items)) return [];
  const q = String(query || '').trim().toLowerCase();
  if (!q) return items;
  return items.filter((item) => bulkSearchHaystack(item).includes(q));
}

module.exports = {
  bulkGroupKey,
  aggregateBulkInventory,
  filterBulkItems,
  bulkSearchHaystack,
  resolveItemAmount,
  canonicalBulkName,
};
