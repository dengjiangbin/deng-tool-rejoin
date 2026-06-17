'use strict';

/**
 * Server-side Ruby Gemstone top-card counter (Phase 9).
 *
 * This is a faithful port of the frontend authoritative counter
 * (site/src/inventory/fishit_tracker.source.ejs :: getRubyGemstoneTopCardCount)
 * so the precomputed snapshot can carry an authoritative
 * `topCards.rubyGemstone.count` + matched-row proof that agrees with the
 * client. The real Fish It "Ruby" payload is a FISH whose base/clean name is
 * "Ruby" carrying the per-instance mutation "Gemstone" (card-level mutation is
 * usually null), so we must expand ownedInstances and merge instance over card.
 */

function normalizeToken(value) {
  return String(value == null ? '' : value)
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ');
}

const RUBY_FISH_NAME_ALIASES = new Set(['ruby']);
const GEMSTONE_MUTATION_ALIASES = new Set(['gemstone', 'gem stone', 'ruby gemstone']);

function isRubyGemstoneFishInstance(row) {
  if (!row || typeof row !== 'object') return false;
  const nameCandidates = [
    row.cleanName,
    row.baseFishName,
    row.fishName,
    row.name,
    row.displayName,
    row.itemName,
  ].map(normalizeToken);
  const mutationCandidates = [
    row.mutation,
    row.mutationName,
    row.mutationType,
    row.metadataMutation,
    row.modifier,
  ].map(normalizeToken);
  const isRubyName = nameCandidates.some((name) => RUBY_FISH_NAME_ALIASES.has(name));
  const isGemstoneMutation = mutationCandidates.some((m) => GEMSTONE_MUTATION_ALIASES.has(m));
  return isRubyName && isGemstoneMutation;
}

function resolveItemAmount(item) {
  if (!item || typeof item !== 'object') return 0;
  const amount = Number(
    item.amount != null ? item.amount
      : item.count != null ? item.count
        : item.quantity != null ? item.quantity
          : 1,
  );
  return Number.isFinite(amount) && amount > 0 ? amount : 1;
}

function rubyGemstoneCountForItem(item) {
  if (!item || typeof item !== 'object') return 0;
  const list = Array.isArray(item.ownedInstances) ? item.ownedInstances : null;
  if (list && list.length) {
    let n = 0;
    for (const inst of list) {
      if (!inst || typeof inst !== 'object') continue;
      const merged = {
        cleanName: inst.cleanName != null ? inst.cleanName : item.cleanName,
        baseFishName: inst.baseFishName != null ? inst.baseFishName : item.baseFishName,
        fishName: inst.fishName != null ? inst.fishName : item.fishName,
        name: inst.name != null ? inst.name : item.name,
        displayName: inst.displayName != null ? inst.displayName : item.displayName,
        itemName: inst.itemName != null ? inst.itemName : item.itemName,
        mutation: inst.mutation != null ? inst.mutation : item.mutation,
        mutationName: inst.mutationName != null ? inst.mutationName : item.mutationName,
        mutationType: inst.mutationType != null ? inst.mutationType : item.mutationType,
        metadataMutation: inst.metadataMutation != null ? inst.metadataMutation : item.metadataMutation,
        modifier: inst.modifier != null ? inst.modifier : item.modifier,
      };
      if (isRubyGemstoneFishInstance(merged)) {
        const qty = Number(
          inst.quantity != null ? inst.quantity
            : inst.amount != null ? inst.amount
              : inst.count != null ? inst.count
                : 1,
        );
        n += Number.isFinite(qty) && qty > 0 ? Math.floor(qty) : 1;
      }
    }
    if (n > 0) return n;
  }
  if (isRubyGemstoneFishInstance(item)) {
    const amount = Number(resolveItemAmount(item));
    return Number.isFinite(amount) && amount > 0 ? Math.floor(amount) : 1;
  }
  return 0;
}

function collectRows(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') return [];
  const fish = Array.isArray(snapshot.fishItems) ? snapshot.fishItems
    : (Array.isArray(snapshot.publicFishItems) ? snapshot.publicFishItems
      : (Array.isArray(snapshot.lastGoodPublicFishItems) ? snapshot.lastGoodPublicFishItems : []));
  const stones = Array.isArray(snapshot.stoneItems) ? snapshot.stoneItems : [];
  const totems = Array.isArray(snapshot.totemItems) ? snapshot.totemItems : [];
  return [].concat(fish, stones, totems);
}

/**
 * Authoritative count + matched-row proof for a precomputed get-backpack body.
 * Returns { count, matchedRows: [{ name, cleanName, mutation, count }] }.
 */
function computeRubyGemstoneTopCard(snapshot) {
  const rows = collectRows(snapshot);
  const matchedRows = [];
  let total = 0;
  for (const row of rows) {
    const c = rubyGemstoneCountForItem(row);
    if (c > 0) {
      total += c;
      matchedRows.push({
        name: row && row.name,
        cleanName: row && row.cleanName,
        mutation: (row && (row.mutation || row.mutationName))
          || (Array.isArray(row && row.ownedInstances) && row.ownedInstances[0]
            ? (row.ownedInstances[0].mutationName || row.ownedInstances[0].mutation)
            : null),
        count: c,
      });
    }
  }
  return { count: total, matchedRows };
}

module.exports = {
  normalizeToken,
  isRubyGemstoneFishInstance,
  rubyGemstoneCountForItem,
  computeRubyGemstoneTopCard,
  RUBY_FISH_NAME_ALIASES,
  GEMSTONE_MUTATION_ALIASES,
};
