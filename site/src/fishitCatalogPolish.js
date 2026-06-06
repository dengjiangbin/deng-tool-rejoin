'use strict';
/**
 * BLOCKER10U — global catalog name repair, conflict recompute, polish stats.
 */

const catchNameParser = require('./fishitCatchNameParser');
const rarityLabels = require('./fishitRarityLabels');

const _stats = {
  enabled: true,
  repairedEntriesCount: 0,
  repairedWeightSuffixCount: 0,
  repairedMutationPrefixCount: 0,
  repairedConflictCount: 0,
};
const _nameProof = [];

function resetStats() {
  _stats.repairedEntriesCount = 0;
  _stats.repairedWeightSuffixCount = 0;
  _stats.repairedMutationPrefixCount = 0;
  _stats.repairedConflictCount = 0;
  _nameProof.length = 0;
}

function recordProof(entry) {
  if (_nameProof.length < 40) _nameProof.push(entry);
}

function repairCatalogEntry(entry, { idField = 'fishName' } = {}) {
  if (!entry) return { changed: false, entry };
  const rawName = entry[idField] || entry.name || entry.fishName;
  if (!rawName) return { changed: false, entry };

  const canon = catchNameParser.canonicalizeFishName(rawName, {
    rarity: entry.rarity || entry.rarityCandidate,
    mutation: entry.mutation,
    weightKg: entry.weightKg,
  });

  let changed = false;
  if (canon.baseFishName && canon.baseFishName !== rawName) {
    entry[idField] = canon.baseFishName;
    if (entry.name != null) entry.name = canon.baseFishName;
    entry.fishName = canon.baseFishName;
    changed = true;
    if (canon.reason === 'weight_suffix_stripped') _stats.repairedWeightSuffixCount += 1;
    if (canon.reason === 'mutation_prefix_stripped') _stats.repairedMutationPrefixCount += 1;
  }

  if (canon.baseFishName) {
    if (entry.baseFishName !== canon.baseFishName) { entry.baseFishName = canon.baseFishName; changed = true; }
    if (canon.displayName && entry.displayName !== canon.displayName) {
      entry.displayName = canon.displayName;
      changed = true;
    }
    if (canon.mutation && entry.mutation !== canon.mutation) {
      entry.mutation = canon.mutation;
      changed = true;
    }
    if (canon.weightKg != null && entry.weightKg == null) {
      entry.weightKg = canon.weightKg;
      changed = true;
    }
  }

  if (Array.isArray(entry.conflictNames) && entry.conflictNames.length) {
    const bases = new Set(
      entry.conflictNames.map((n) => catchNameParser.baseFishNameForConflict(n) || n).filter(Boolean),
    );
    if (canon.baseFishName) bases.add(canon.baseFishName);
    if (bases.size <= 1) {
      entry.conflictNames = null;
      if (entry.confidence === 'conflict' && !entry.blockedReason) {
        entry.confidence = 'confirmed';
        entry.publicEligible = true;
        entry.confirmationReason = entry.confirmationReason || 'normalized_base_name_repair';
        _stats.repairedConflictCount += 1;
      }
      changed = true;
    } else {
      const next = [...bases];
      if (next.length !== entry.conflictNames.length
          || next.some((b, i) => entry.conflictNames[i] !== b)) {
        entry.conflictNames = next;
        changed = true;
      }
    }
  }

  if (canon.baseFishName && rarityLabels.isBlockedLearnName(canon.baseFishName)) {
    entry.publicEligible = false;
    if (entry.confidence !== 'blocked') entry.confidence = 'blocked';
    changed = true;
  }

  if (changed) {
    _stats.repairedEntriesCount += 1;
    recordProof({
      itemId: entry.itemId || entry.normalizedItemId || null,
      rawName,
      baseFishName: canon.baseFishName,
      displayName: canon.displayName,
      mutation: canon.mutation,
      rarity: entry.rarity || canon.rarity || null,
      weightKg: canon.weightKg,
      changed: true,
      reason: canon.reason,
    });
  }

  return { changed, entry, canon };
}

function repairAllEntries(byItemId) {
  resetStats();
  if (!byItemId || typeof byItemId !== 'object') return false;
  let any = false;
  for (const entry of Object.values(byItemId)) {
    const { changed } = repairCatalogEntry(entry);
    if (changed) any = true;
  }
  return any;
}

/** Clean public-facing item name — never show weight in name. */
function polishPublicItem(item) {
  if (!item || typeof item !== 'object') return item;
  const raw = item.name || item.displayName || '';
  if (!raw || /^Item #\d+$/i.test(raw)) return item;

  const canon = catchNameParser.canonicalizeFishName(raw, {
    mutation: item.mutation,
    rarity: item.rarity,
    weightKg: item.weightKg != null ? item.weightKg : item.weight,
  });

  const displayName = item.mutation
    ? (item.displayName || (canon.mutation ? `${canon.mutation} ${canon.baseFishName}` : canon.displayName))
    : (canon.displayName || canon.baseFishName || raw);

  const cardName = canon.baseFishName
    ? (item.mutation || canon.mutation
      ? `${item.mutation || canon.mutation} ${canon.baseFishName}`
      : canon.baseFishName)
    : displayName;

  const weight = item.weightKg != null ? item.weightKg
    : (canon.weightKg != null ? canon.weightKg : item.weight);

  return {
    ...item,
    name: cardName,
    displayName: cardName,
    baseFishName: canon.baseFishName || item.baseFishName || cardName,
    mutation: item.mutation || canon.mutation || null,
    weight: weight != null ? weight : item.weight,
    weightKg: weight != null ? weight : item.weightKg,
    shiny: item.shiny === true
      || String(item.mutation || canon.mutation || '').toLowerCase().includes('shiny'),
  };
}

function polishPublicFishItems(items) {
  if (!Array.isArray(items)) return [];
  return items.map(polishPublicItem);
}

function getCatalogPolishStats(imageCacheStats, rarityStats) {
  return {
    enabled: true,
    repairedEntriesCount: _stats.repairedEntriesCount,
    repairedWeightSuffixCount: _stats.repairedWeightSuffixCount,
    repairedMutationPrefixCount: _stats.repairedMutationPrefixCount,
    repairedConflictCount: _stats.repairedConflictCount,
    imageCacheCount: imageCacheStats?.cachedCount || 0,
    imageCacheMissingCount: imageCacheStats?.missingCount || 0,
    rarityKnownCount: rarityStats?.knownCount || 0,
    rarityMissingCount: rarityStats?.missingCount || 0,
  };
}

function getNameNormalizationProof(limit = 25) {
  return _nameProof.slice(0, limit);
}

/** Repair poisoned catalogStore keys/names on load (BLOCKER10U). */
function repairCatalogStoreEntries(catalog, idIndex) {
  if (!catalog || !catalog.entries) return false;
  const fishImageAssets = require('./fishitFishImageAssets');
  let changed = false;
  const toRekey = [];

  for (const [key, entry] of Object.entries(catalog.entries)) {
    if (!entry || !entry.name) continue;
    const canon = catchNameParser.canonicalizeFishName(entry.name);
    if (!canon.baseFishName) continue;
    const newKey = fishImageAssets.normalizeName(canon.baseFishName);
    const needs = newKey !== key
      || canon.baseFishName !== entry.name
      || (canon.mutation && entry.mutation !== canon.mutation)
      || (canon.weightKg != null && entry.weightKg == null);
    if (!needs) continue;

    toRekey.push({
      oldKey: key,
      newKey,
      entry: {
        ...entry,
        name: canon.baseFishName,
        key: newKey,
        displayName: canon.displayName || canon.baseFishName,
        mutation: canon.mutation || entry.mutation || null,
        weightKg: canon.weightKg != null ? canon.weightKg : entry.weightKg,
      },
      canon,
    });
  }

  for (const row of toRekey) {
    delete catalog.entries[row.oldKey];
    const existing = catalog.entries[row.newKey];
    if (!existing) {
      catalog.entries[row.newKey] = row.entry;
    } else {
      catalog.entries[row.newKey] = {
        ...existing,
        ...row.entry,
        name: row.entry.name,
        itemId: existing.itemId || row.entry.itemId,
      };
    }
    if (row.entry.itemId) idIndex[String(row.entry.itemId)] = row.newKey;
    changed = true;
    _stats.repairedEntriesCount += 1;
    if (row.canon.reason === 'weight_suffix_stripped') _stats.repairedWeightSuffixCount += 1;
    if (row.canon.reason === 'mutation_prefix_stripped') _stats.repairedMutationPrefixCount += 1;
    recordProof({
      itemId: row.entry.itemId || null,
      rawName: row.oldKey,
      baseFishName: row.canon.baseFishName,
      displayName: row.canon.displayName,
      mutation: row.canon.mutation,
      rarity: row.entry.tier || null,
      weightKg: row.canon.weightKg,
      changed: true,
      reason: row.canon.reason,
    });
  }

  return changed;
}

module.exports = {
  repairCatalogEntry,
  repairAllEntries,
  repairCatalogStoreEntries,
  polishPublicItem,
  polishPublicFishItems,
  getCatalogPolishStats,
  getNameNormalizationProof,
  resetStats,
};
