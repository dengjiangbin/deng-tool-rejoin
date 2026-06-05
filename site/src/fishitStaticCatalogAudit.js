'use strict';
/**
 * Audit static catalog sources for BLOCKER10O.
 * Reports what itemId/name/rarity/image data exists without inventing mappings.
 */

const path = require('path');
const fs = require('fs');
const catalogStore = require('./fishitCatalogStore');

const SOURCE_DEFS = [
  {
    id: 'seed_confirmed',
    path: null,
    type: 'inline_seeds',
    getEntries: () => catalogStore.KNOWN_ID_SEEDS.filter((e) => catalogStore.isFishCategory(e.category)),
  },
  {
    id: 'fishit_fish_confirmed_catalog',
    path: path.join(__dirname, '..', 'data', 'fishit_fish_confirmed_catalog.json'),
    type: 'json_file',
  },
  {
    id: 'fishit_catalog',
    path: path.join(__dirname, '..', 'data', 'fishit_catalog.json'),
    type: 'json_file',
  },
  {
    id: 'fishit_learned_fish_catalog',
    path: path.join(__dirname, '..', 'data', 'fishit_learned_fish_catalog.json'),
    type: 'json_file',
  },
  {
    id: 'fishit_fish_image_assets',
    path: path.join(__dirname, '..', 'data', 'fishit_fish_image_assets.json'),
    type: 'json_file_name_only',
  },
  {
    id: 'fishit_image_assets',
    path: path.join(__dirname, '..', 'data', 'fishit_image_assets.json'),
    type: 'json_file_name_only',
  },
  {
    id: 'fishit_rod_assets',
    path: path.join(__dirname, '..', 'data', 'fishit_rod_assets.json'),
    type: 'json_file_rods',
  },
];

function readJsonSafe(filePath) {
  try {
    if (!fs.existsSync(filePath)) return null;
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (err) {
    return { _parseError: err.message || 'parse_error' };
  }
}

function extractFishRows(parsed, type) {
  if (!parsed || parsed._parseError) return { rows: [], error: parsed?._parseError || 'missing' };
  if (type === 'json_file_name_only') {
    const list = Array.isArray(parsed) ? parsed
      : (Array.isArray(parsed.fish) ? parsed.fish : (Array.isArray(parsed.entries) ? parsed.entries : []));
    return { rows: list, error: null, nameOnly: true };
  }
  if (type === 'json_file_rods') {
    return { rows: [], error: 'rod_catalog_not_fish_itemid', skip: true };
  }
  if (type === 'json_file') {
    if (parsed.byItemId && typeof parsed.byItemId === 'object') {
      return { rows: Object.values(parsed.byItemId), error: null };
    }
    if (parsed.entries && typeof parsed.entries === 'object') {
      return { rows: Object.values(parsed.entries), error: null };
    }
    const list = Array.isArray(parsed.fish) ? parsed.fish : (Array.isArray(parsed.entries) ? parsed.entries : []);
    return { rows: list, error: null };
  }
  return { rows: [], error: 'unknown_type' };
}

function rowHasItemId(row) {
  return row && row.itemId != null && /^\d+$/.test(String(row.itemId).trim());
}

function rowHasRarity(row) {
  const r = row && (row.rarity || row.tier);
  return !!(r && String(r).trim() && String(r).toLowerCase() !== 'unknown');
}

function rowHasImage(row) {
  if (row && row.imageAssetId && /^\d{10,22}$/.test(String(row.imageAssetId))) return true;
  if (row && row.assetId && /^\d{10,22}$/.test(String(row.assetId))) return true;
  if (row && row.imageUrl && /^https?:\/\//i.test(String(row.imageUrl))) return true;
  return false;
}

function auditStaticCatalogSources() {
  const sources = [];
  const rejected = [];
  let candidateCount = 0;
  let confirmedCount = 0;
  let withItemId = 0;
  let withRarity = 0;
  let withImage = 0;

  for (const def of SOURCE_DEFS) {
    if (def.getEntries) {
      const rows = def.getEntries();
      const fishRows = rows.filter((r) => r && catalogStore.isFishCategory(r.category || 'fish'));
      const itemIds = fishRows.filter(rowHasItemId).length;
      const rarities = fishRows.filter(rowHasRarity).length;
      const images = fishRows.filter((r) => rowHasImage(r) || !!r.name).length;
      candidateCount += fishRows.length;
      confirmedCount += fishRows.length;
      withItemId += itemIds;
      withRarity += rarities;
      withImage += images;
      sources.push({
        id: def.id,
        type: def.type,
        exists: true,
        fishCount: fishRows.length,
        withItemId: itemIds,
        withRarity: rarities,
        withImage: images,
        imported: true,
      });
      continue;
    }

    const parsed = readJsonSafe(def.path);
    if (!parsed) {
      rejected.push({ id: def.id, reason: 'file_missing', path: def.path });
      continue;
    }
    if (parsed._parseError) {
      rejected.push({ id: def.id, reason: 'parse_error', detail: parsed._parseError });
      continue;
    }

    const { rows, error, nameOnly, skip } = extractFishRows(parsed, def.type);
    if (skip) {
      rejected.push({ id: def.id, reason: error });
      continue;
    }
    if (error) {
      rejected.push({ id: def.id, reason: error });
      continue;
    }

    const fishRows = nameOnly
      ? rows.filter((r) => r && r.name)
      : rows.filter((r) => r && (catalogStore.isFishCategory(r.category) || !r.category || r.category === 'fish'));

    const itemIds = nameOnly ? 0 : fishRows.filter(rowHasItemId).length;
    const rarities = fishRows.filter(rowHasRarity).length;
    const images = fishRows.filter(rowHasImage).length;

    candidateCount += fishRows.length;
    if (itemIds > 0) confirmedCount += fishRows.filter(rowHasItemId).length;
    withItemId += itemIds;
    withRarity += rarities;
    withImage += images;

    if (nameOnly && fishRows.length > 0) {
      sources.push({
        id: def.id,
        type: def.type,
        exists: true,
        fishCount: fishRows.length,
        withItemId: 0,
        withRarity: 0,
        withImage: images,
        imported: def.id === 'fishit_fish_image_assets',
        note: 'name_to_asset_only_no_itemid',
      });
    } else {
      sources.push({
        id: def.id,
        type: def.type,
        exists: true,
        fishCount: fishRows.length,
        withItemId: itemIds,
        withRarity: rarities,
        withImage: images,
        imported: def.id !== 'fishit_image_assets',
      });
      if (def.id === 'fishit_image_assets') {
        rejected.push({ id: def.id, reason: 'unused_filename_not_imported' });
      }
    }
  }

  // Sibling bot DB — report as external, not itemId-mapped
  const botDbPath = path.join(__dirname, '..', '..', '..', 'DENG Fish It', 'data', 'deng-fish-it.sqlite');
  if (fs.existsSync(botDbPath)) {
    sources.push({
      id: 'deng_fish_it_sqlite',
      type: 'external_sqlite',
      exists: true,
      withItemId: 0,
      withRarity: 0,
      withImage: 0,
      imported: 'partial_name_image_only',
      note: 'fish_catalog_seen_and_alltime_fish_cache_name_only',
    });
  } else {
    rejected.push({ id: 'deng_fish_it_sqlite', reason: 'file_missing', path: botDbPath });
  }

  return {
    staticCatalogSources: sources.map((s) => s.id),
    staticCatalogCandidateCount: candidateCount,
    staticCatalogConfirmedCount: confirmedCount,
    staticCatalogWithItemId: withItemId,
    staticCatalogWithRarity: withRarity,
    staticCatalogWithImage: withImage,
    rejectedStaticCatalogSources: rejected,
    sourcesDetail: sources,
    summary: confirmedCount > 5
      ? 'itemId_catalog_partially_present'
      : 'no_bulk_itemId_catalog_only_5_seeds_plus_learning',
  };
}

module.exports = { auditStaticCatalogSources, SOURCE_DEFS };
