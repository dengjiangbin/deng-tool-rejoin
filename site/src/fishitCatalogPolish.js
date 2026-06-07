'use strict';
/**
 * BLOCKER10U — global catalog name repair, conflict recompute, polish stats.
 */

const catchNameParser = require('./fishitCatchNameParser');
const rarityLabels = require('./fishitRarityLabels');
const protectedFishNames = require('./fishitProtectedFishNames');

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

/** Clean public-facing item name — card title is baseFishName only (BLOCKER10U3-U4). */
function polishPublicItem(item) {
  if (!item || typeof item !== 'object') return item;
  const raw = item.name || item.displayName || '';
  if (protectedFishNames.isProtectedBaseName(item.baseFishName)
      || protectedFishNames.isProtectedBaseName(raw)) {
    const base = protectedFishNames.normalizeProtected(item.baseFishName || raw);
    const mutation = item.mutation || null;
    const displayName = item.displayName
      || (mutation ? `${mutation} ${base}` : base);
    return {
      ...item,
      cardName: base,
      name: base,
      baseFishName: base,
      displayName,
      mutation,
      weight: item.weightKg != null ? item.weightKg : item.weight,
      weightKg: item.weightKg != null ? item.weightKg : item.weight,
      shiny: item.shiny === true || String(mutation || '').toLowerCase().includes('shiny'),
    };
  }
  const catalogLocked = item.catalogSource === 'manual_verified_catalog'
    || item.catalogSource === 'canonical_catalog';
  const lockedBase = item.baseFishName
    && (catalogLocked || !startsWithMutationPrefix(item.baseFishName))
    ? String(item.baseFishName).trim()
    : null;

  if (lockedBase && (catalogLocked || raw === lockedBase || /^Item #\d+$/i.test(raw))) {
    const mutation = item.mutation || null;
    const displayName = item.displayName
      || (mutation ? `${mutation} ${lockedBase}` : raw || lockedBase);
    return {
      ...item,
      cardName: lockedBase,
      name: lockedBase,
      baseFishName: lockedBase,
      displayName,
      mutation,
      weight: item.weightKg != null ? item.weightKg : item.weight,
      weightKg: item.weightKg != null ? item.weightKg : item.weight,
      shiny: item.shiny === true || String(mutation || '').toLowerCase().includes('shiny'),
    };
  }

  if (!raw || /^Item #\d+$/i.test(raw)) {
    if (item.baseFishName) {
      const base = String(item.baseFishName).trim();
      const mut = item.mutation || null;
      const fullDisplay = mut ? `${mut} ${base}` : base;
      return {
        ...item,
        cardName: base,
        name: base,
        baseFishName: base,
        displayName: item.displayName || fullDisplay,
        mutation: mut,
        shiny: item.shiny === true || String(mut || '').toLowerCase().includes('shiny'),
      };
    }
    return item;
  }

  const canon = catchNameParser.canonicalizeFishName(raw, {
    mutation: item.mutation,
    rarity: item.rarity,
    weightKg: item.weightKg != null ? item.weightKg : item.weight,
  });

  const baseFishName = canon.baseFishName || item.baseFishName || null;
  const mutation = item.mutation || canon.mutation || null;
  const displayName = mutation && baseFishName
    ? `${mutation} ${baseFishName}`
    : (item.displayName || canon.displayName || baseFishName || raw);
  const cardName = baseFishName || raw;

  const weight = item.weightKg != null ? item.weightKg
    : (canon.weightKg != null ? canon.weightKg : item.weight);

  return {
    ...item,
    cardName,
    name: cardName,
    baseFishName: baseFishName || cardName,
    displayName,
    mutation,
    weight: weight != null ? weight : item.weight,
    weightKg: weight != null ? weight : item.weightKg,
    shiny: item.shiny === true
      || String(mutation || '').toLowerCase().includes('shiny'),
  };
}

const MUTATION_PREFIXES = [
  'Fairy Dust', 'Radioactive Shiny', 'Shiny', 'Big', 'Ghost', 'Holographic', 'Sandy',
  'Galaxy', 'Radioactive', 'Albino', 'Darkened', 'Electric', 'Frozen', 'Mythic', 'Glossy',
  'Baby', 'Giant', 'Golden', 'Silver', 'Mosaic', 'Corrupt', 'Midnight',
];

function startsWithMutationPrefix(name) {
  const s = String(name || '').trim();
  if (!s) return false;
  if (protectedFishNames.isProtectedBaseName(s)) return false;
  const low = s.toLowerCase();
  for (const prefix of MUTATION_PREFIXES) {
    if (low.startsWith(`${prefix.toLowerCase()} `)) return true;
  }
  return false;
}

function buildPublicNameContractProof(items, limit = 25) {
  return (items || []).slice(0, limit).map((item) => {
    const publicName = item.cardName || item.name;
    const base = item.baseFishName || publicName;
    const mut = item.mutation || null;
    return {
      itemId: item.itemId || null,
      rawName: item.rawName || null,
      finalName: item.name || null,
      displayName: item.displayName || null,
      baseFishName: base,
      mutation: mut,
      publicName,
      cardName: item.cardName || base,
      titleUsesBaseName: !startsWithMutationPrefix(publicName),
      mutationSeparated: !mut || (publicName && !String(publicName).toLowerCase().startsWith(String(mut).toLowerCase())),
    };
  });
}

function polishPublicFishItems(items) {
  if (!Array.isArray(items)) return [];
  return items.map(polishPublicItem);
}

function normalizeMutationGroup(item) {
  const mut = item?.mutation || null;
  if (!mut) return '__base__';
  return String(mut).toLowerCase().trim();
}

/** Public card aggregation key: canonical species + mutation group (BLOCKER10W). */
function publicAggregationKey(item) {
  const speciesId = item?.speciesId || item?.globalSpeciesId || null;
  const base = String(item?.baseFishName || item?.cardName || item?.name || '').trim();
  const normBase = base.toLowerCase().replace(/[^\w\s]/g, '').replace(/\s+/g, ' ').trim();
  const mutGroup = normalizeMutationGroup(item);
  return `${speciesId || normBase}::${mutGroup}`;
}

/**
 * Group duplicate species cards — sum amounts, keep weight internal/debug only.
 * Same base species with different weights becomes one public card.
 */
function groupPublicFishItems(items) {
  if (!Array.isArray(items)) return [];
  const groups = new Map();
  for (const raw of items) {
    const item = polishPublicItem(raw);
    const key = publicAggregationKey(item);
    const amt = Number(item.amount) > 0 ? Math.floor(Number(item.amount)) : 1;
    const w = item.weightKg != null ? Number(item.weightKg) : (item.weight != null ? Number(item.weight) : null);
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, {
        ...item,
        amount: amt,
        groupedInstanceCount: 1,
        _weightSamples: Number.isFinite(w) ? [w] : [],
      });
      continue;
    }
    existing.amount = (Number(existing.amount) || 1) + amt;
    existing.groupedInstanceCount = (existing.groupedInstanceCount || 1) + 1;
    if (Number.isFinite(w)) existing._weightSamples.push(w);
    if (!existing.imageUrl && item.imageUrl) {
      existing.imageUrl = item.imageUrl;
      existing.imageSource = item.imageSource;
      existing.imageStatus = item.imageStatus;
    }
    if ((!existing.rarity || existing.rarity === 'Unknown') && item.rarity && item.rarity !== 'Unknown') {
      existing.rarity = item.rarity;
      existing.raritySource = item.raritySource;
    }
  }
  return [...groups.values()].map((g) => {
    const samples = g._weightSamples || [];
    const { _weightSamples, weight, weightKg, ...rest } = g;
    return {
      ...rest,
      publicWeightHidden: true,
      debugWeight: samples.length ? {
        totalWeightKg: samples.reduce((a, b) => a + b, 0),
        minWeightKg: Math.min(...samples),
        maxWeightKg: Math.max(...samples),
        sampleWeights: samples.slice(0, 8),
        instances: samples.length,
      } : null,
    };
  });
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
  groupPublicFishItems,
  publicAggregationKey,
  normalizeMutationGroup,
  getCatalogPolishStats,
  getNameNormalizationProof,
  buildPublicNameContractProof,
  startsWithMutationPrefix,
  MUTATION_PREFIXES,
  resetStats,
};
