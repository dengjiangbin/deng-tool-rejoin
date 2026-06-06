'use strict';
/**
 * BLOCKER10U — rarity enrichment from reliable catalog sources only.
 */

const fishCatalog = require('./fishitFishCatalog');
const globalFishCatalog = require('./fishitGlobalFishItemCatalog');
const catalogStore = require('./fishitCatalogStore');
const catchNameParser = require('./fishitCatchNameParser');
const rarityLabels = require('./fishitRarityLabels');

const _proof = [];
const _sourcesUsed = new Set();

function resetProof() {
  _proof.length = 0;
  _sourcesUsed.clear();
}

function recordProof(row) {
  if (_proof.length < 40) _proof.push(row);
  if (row.raritySource) _sourcesUsed.add(row.raritySource);
}

function lookupRarityForItem(item) {
  if (!item) return null;
  const itemId = item.itemId ? String(item.itemId).trim() : null;
  const baseName = item.baseFishName
    || catchNameParser.canonicalizeFishName(item.name || '').baseFishName
    || item.name;

  if (baseName && rarityLabels.isRarityLabel(baseName)
      && !catchNameParser.MUTATION_LABELS.has(String(baseName).toLowerCase())) {
    return null;
  }

  if (itemId) {
    const confirmed = fishCatalog.lookupByItemId(itemId);
    if (confirmed && confirmed.rarity) {
      return {
        rarity: fishCatalog.normalizeRarity(confirmed.rarity),
        raritySource: confirmed.source || 'confirmed_catalog',
        rarityConfidence: 'confirmed',
      };
    }

    const global = globalFishCatalog.lookupById(itemId);
    if (global && global.rarity) {
      return {
        rarity: fishCatalog.normalizeRarity(global.rarity),
        raritySource: global.raritySources?.[0] || global.source || 'global_catalog',
        rarityConfidence: global.rarityConfidence || 'pending',
      };
    }

    const store = catalogStore.lookupById(itemId);
    if (store && store.tier) {
      const norm = fishCatalog.normalizeRarity(store.tier);
      if (norm) {
        return {
          rarity: norm,
          raritySource: store.source || 'catalog_store',
          rarityConfidence: 'pending',
        };
      }
    }
  }

  if (baseName) {
    const byName = fishCatalog.lookupByName(baseName);
    if (byName && byName.rarity) {
      return {
        rarity: fishCatalog.normalizeRarity(byName.rarity),
        raritySource: byName.source || 'confirmed_catalog_name',
        rarityConfidence: 'confirmed',
      };
    }
  }

  return null;
}

function attachRarityFields(item) {
  if (!item || typeof item !== 'object') return item;
  if (item.rarity && item.raritySource) return item;

  const hit = lookupRarityForItem(item);
  if (!hit || !hit.rarity) {
    recordProof({
      itemId: item.itemId || null,
      baseFishName: item.baseFishName || item.name,
      rarity: null,
      raritySource: null,
      confidence: null,
    });
    return item;
  }

  recordProof({
    itemId: item.itemId || null,
    baseFishName: item.baseFishName || item.name,
    rarity: hit.rarity,
    raritySource: hit.raritySource,
    confidence: hit.rarityConfidence,
  });

  return {
    ...item,
    rarity: hit.rarity,
    tier: hit.rarity,
    raritySource: hit.raritySource,
    rarityConfidence: hit.rarityConfidence,
    rarityUpdatedAt: new Date().toISOString(),
  };
}

function attachRarityToItems(items) {
  if (!Array.isArray(items)) return [];
  return items.map(attachRarityFields);
}

function getRarityResolutionProof(limit = 25) {
  return _proof.slice(0, limit);
}

function getRarityStats(items) {
  const list = Array.isArray(items) ? items : [];
  const known = list.filter((i) => i && i.rarity);
  return {
    knownCount: known.length,
    missingCount: list.length - known.length,
    raritySourcesUsed: [..._sourcesUsed],
    rarityCatalogCount: fishCatalog.getStats().fishCatalogWithRarity,
  };
}

module.exports = {
  lookupRarityForItem,
  attachRarityFields,
  attachRarityToItems,
  getRarityResolutionProof,
  getRarityStats,
  resetProof,
};
