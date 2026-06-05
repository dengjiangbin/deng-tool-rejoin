'use strict';
/**
 * Name-only fish catalog validation (BLOCKER10P).
 * Uses image asset list, confirmed catalog, and optional Fish It DB — no itemId binding.
 */

const rarityLabels = require('./fishitRarityLabels');
const fishImageAssets = require('./fishitFishImageAssets');
const fishCatalog = require('./fishitFishCatalog');

let fishitDb = null;
try { fishitDb = require('./fishitDb'); } catch (_) { fishitDb = null; }

function validateFishName(name) {
  const display = String(name || '').trim();
  if (!display) {
    return { valid: false, reason: 'empty_name', nameKnown: false, learnedName: null };
  }
  if (rarityLabels.isRarityLabel(display)) {
    return {
      valid: false,
      reason: 'name_is_rarity_label',
      nameKnown: false,
      learnedName: display,
      rarityCandidate: display,
    };
  }
  if (rarityLabels.isGenericStatusLabel(display)) {
    return {
      valid: false,
      reason: 'name_is_status_label',
      nameKnown: false,
      learnedName: display,
    };
  }

  const img = fishImageAssets.lookupByFishName(display);
  if (img && img.assetId) {
    return {
      valid: true,
      reason: 'fish_image_asset_catalog',
      nameKnown: true,
      learnedName: display,
      imageAssetId: img.assetId,
      imageSource: img.imageSource,
    };
  }

  const confirmed = fishCatalog.lookupByName(display);
  if (confirmed) {
    return {
      valid: true,
      reason: 'confirmed_itemid_catalog',
      nameKnown: true,
      learnedName: display,
      rarity: confirmed.rarity || null,
      imageAssetId: confirmed.imageAssetId || null,
    };
  }

  if (fishitDb && typeof fishitDb.resolveSpeciesImageSource === 'function') {
    const hit = fishitDb.resolveSpeciesImageSource(display, null);
    if (hit && hit.url) {
      return {
        valid: true,
        reason: hit.source || 'name_only_db',
        nameKnown: true,
        learnedName: display,
        imageUrl: hit.url,
      };
    }
  }

  return {
    valid: false,
    reason: 'name_not_in_catalog',
    nameKnown: false,
    learnedName: display,
  };
}

function getNameCatalogStats() {
  const sources = [];
  const nameSet = new Set();
  let knownFishImageCount = fishImageAssets.getCatalogEntryCount();
  if (knownFishImageCount > 0) sources.push('fish_image_asset_catalog');

  const confirmed = fishCatalog.getAllFish();
  for (const f of confirmed) {
    if (f && f.name) nameSet.add(f.name.toLowerCase());
  }

  let dbImageCount = 0;
  if (fishitDb && typeof fishitDb.buildImageIndex === 'function') {
    try {
      const idx = fishitDb.buildImageIndex();
      dbImageCount = idx ? idx.size : 0;
      if (dbImageCount > 0) {
        sources.push('fishit_db_image_index');
        for (const key of idx.keys()) nameSet.add(String(key).toLowerCase());
      }
    } catch (_) { /* optional */ }
  }

  const catalogNameOnlyCount = nameSet.size;

  return {
    catalogNameOnlyCount,
    knownFishNameCount: nameSet.size,
    knownFishImageCount: knownFishImageCount + dbImageCount,
    nameOnlyCatalogSources: sources.length ? sources : ['seed_confirmed_only'],
  };
}

function buildLearningValidation(learnedCatalog) {
  const blocked = learnedCatalog.getBlockedMappings();
  return {
    rarityLabelsBlocked: rarityLabels.getRarityLabelsBlocked(),
    blockedLearnedMappings: blocked,
    ...getNameCatalogStats(),
    pendingMappings: learnedCatalog.getAllMappings().filter((m) => !m.publicEligible),
    confirmedMappings: learnedCatalog.getAllMappings().filter((m) => m.publicEligible),
    rejectedMappings: blocked,
  };
}

module.exports = {
  validateFishName,
  getNameCatalogStats,
  buildLearningValidation,
};
