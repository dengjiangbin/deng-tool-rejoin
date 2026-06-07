'use strict';
/**
 * BLOCKER10U — rarity enrichment from reliable catalog sources only.
 */

const fishCatalog = require('./fishitFishCatalog');
const globalFishCatalog = require('./fishitGlobalFishItemCatalog');
const catalogStore = require('./fishitCatalogStore');
const catchNameParser = require('./fishitCatchNameParser');
const rarityLabels = require('./fishitRarityLabels');
const canonicalCatalog = require('./fishitCanonicalCatalog');
let globalCatalogService = null;
try { globalCatalogService = require('./fishitGlobalCatalogService'); } catch (_) { globalCatalogService = null; }
let rarityColorMap = null;
try { rarityColorMap = require('./fishitRarityColorMap'); } catch (_) { rarityColorMap = null; }
let fishitDb = null;
try { fishitDb = require('./fishitDb'); } catch (_) { fishitDb = null; }

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

  // 1. manual_verified global DB mapping/species rarity
  if (globalCatalogService) {
    try {
      if (itemId) {
        const mapping = globalCatalogService.resolveCatalogMetaForItemId(itemId);
        if (mapping?.tier && mapping.confidence === 'manual_verified') {
          return {
            rarity: fishCatalog.normalizeRarity(mapping.tier),
            raritySource: 'manual_verified',
            rarityConfidence: 'manual_verified',
          };
        }
      }
      const globalHit = globalCatalogService.resolveRarityForItem({
        itemId,
        baseFishName: baseName,
        name: item.name,
        displayName: item.displayName,
        cardName: item.cardName,
      });
      if (globalHit?.rarity?.rarity) {
        const src = globalHit.rarity.raritySource || globalCatalogService.SOURCE_GLOBAL;
        const conf = globalHit.rarity.rarityConfidence || 'seed_imported';
        if (conf === 'manual_verified' || src === 'manual_verified_catalog') {
          return {
            rarity: fishCatalog.normalizeRarity(globalHit.rarity.rarity),
            raritySource: 'manual_verified',
            rarityConfidence: 'manual_verified',
          };
        }
      }
    } catch (_) { /* fallback */ }
  }

  // 2. captured in-game UI name color evidence
  if (item.uiRarityFromColor) {
    return {
      rarity: fishCatalog.normalizeRarity(item.uiRarityFromColor),
      raritySource: 'ui_name_color',
      rarityConfidence: item.uiRarityConfidence || 'ui_color',
    };
  }
  if (item.uiNameColor && rarityColorMap) {
    const colorHit = rarityColorMap.resolveRarityFromUiColor(item.uiNameColor);
    if (colorHit?.rarity) {
      return {
        rarity: colorHit.rarity,
        raritySource: 'ui_name_color',
        rarityConfidence: colorHit.confidence || 'ui_color',
      };
    }
  }

  // 3. Quiz Bot / global seed rarity (global DB species)
  if (globalCatalogService) {
    try {
      const globalHit = globalCatalogService.resolveRarityForItem({
        itemId,
        baseFishName: baseName,
        name: item.name,
        displayName: item.displayName,
        cardName: item.cardName,
      });
      if (globalHit?.rarity?.rarity) {
        return {
          rarity: fishCatalog.normalizeRarity(globalHit.rarity.rarity),
          raritySource: globalHit.rarity.raritySource || globalCatalogService.SOURCE_GLOBAL,
          rarityConfidence: globalHit.rarity.rarityConfidence || 'seed_imported',
        };
      }
    } catch (_) { /* fallback */ }
  }

  const canon = canonicalCatalog.resolveForItem({
    itemId,
    baseFishName: baseName,
    name: item.name,
    displayName: item.displayName,
  });
  if (canon?.rarity) {
    return {
      rarity: fishCatalog.normalizeRarity(canon.rarity),
      raritySource: canon.raritySource || 'canonical_catalog',
      rarityConfidence: canon.rarityConfidence || 'confirmed',
    };
  }

  // 5. tracker payload tier/rarity from Replion metadata
  const payloadTier = item.tier || item.rarity;
  if (payloadTier && String(payloadTier).toLowerCase() !== 'unknown') {
    const norm = fishCatalog.normalizeRarity(payloadTier);
    if (norm) {
      return {
        rarity: norm,
        raritySource: 'tracker_payload_tier',
        rarityConfidence: 'live_observed',
      };
    }
  }

  // fishit_db secret/forgotten hints
  if (fishitDb && typeof fishitDb.exportRarityHints === 'function' && baseName) {
    try {
      const hints = fishitDb.exportRarityHints();
      const key = String(baseName).toLowerCase().trim();
      const hit = hints.find((h) => h.normalizedKey === key || String(h.name).toLowerCase() === key);
      if (hit?.rarity) {
        return {
          rarity: fishCatalog.normalizeRarity(hit.rarity),
          raritySource: hit.source || 'fishit_db_fallback',
          rarityConfidence: 'confirmed',
        };
      }
    } catch (_) { /* */ }
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
  if (item.rarity && item.rarity !== 'Unknown') {
    return {
      ...item,
      tier: item.tier || item.rarity,
      raritySource: item.raritySource || null,
      rarityConfidence: item.rarityConfidence || null,
      rarityNeedsData: false,
    };
  }

  const hit = lookupRarityForItem(item);
  if (!hit || !hit.rarity) {
    const miss = canonicalCatalog.resolveForItem(item) || {};
    recordProof({
      itemId: item.itemId || null,
      baseFishName: item.baseFishName || item.name,
      rarity: 'Unknown',
      raritySource: null,
      confidence: null,
      triedAliases: miss.triedAliases || [item.baseFishName, item.name].filter(Boolean),
      searchedSources: miss.searchedSources || [],
    });
    return {
      ...item,
      rarity: 'Unknown',
      tier: 'Unknown',
      raritySource: null,
      rarityConfidence: null,
      rarityNeedsData: true,
    };
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
    rarityNeedsData: false,
    rarityAccentColor: rarityColorMap?.getRarityAccentColor(hit.rarity) || null,
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
