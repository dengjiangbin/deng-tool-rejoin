'use strict';
/**
 * Import fish catalog rows (itemId, baseFishName, rarity, imageAssetId/sourceUrl).
 * Usage: node scripts/import_fish_catalog_assets.js path/to/file.json
 */

const fs = require('fs');
const path = require('path');

const inputPath = process.argv[2];
if (!inputPath) {
  console.error('Usage: node scripts/import_fish_catalog_assets.js <json-or-csv-file>');
  process.exit(1);
}

const canonicalCatalog = require('../site/src/fishitCanonicalCatalog');
const fishCatalog = require('../site/src/fishitFishCatalog');
const fishImageCache = require('../site/src/fishitFishImageCache');

function parseInput(filePath) {
  const raw = fs.readFileSync(filePath, 'utf8').trim();
  if (filePath.toLowerCase().endsWith('.csv')) {
    const lines = raw.split(/\r?\n/).filter(Boolean);
    const header = lines[0].split(',').map((h) => h.trim().toLowerCase());
    const rows = [];
    for (const line of lines.slice(1)) {
      const cols = line.split(',').map((c) => c.trim());
      const row = {};
      header.forEach((h, i) => { row[h] = cols[i]; });
      rows.push({
        itemId: row.itemid || row.item_id || row.itemId,
        baseFishName: row.basefishname || row.name || row.basename,
        rarity: row.rarity || row.tier,
        imageAssetId: row.imageassetid || row.assetid || row.asset_id,
        sourceUrl: row.sourceurl || row.imageurl || row.image_url,
      });
    }
    return rows;
  }
  const parsed = JSON.parse(raw);
  if (Array.isArray(parsed)) return parsed;
  if (Array.isArray(parsed.fish)) return parsed.fish;
  if (Array.isArray(parsed.entries)) return parsed.entries;
  if (Array.isArray(parsed.rows)) return parsed.rows;
  throw new Error('Unsupported JSON shape — expected array or { fish|entries|rows: [] }');
}

async function main() {
  const abs = path.resolve(inputPath);
  if (!fs.existsSync(abs)) {
    console.error('File not found:', abs);
    process.exit(1);
  }

  const rows = parseInput(abs);
  console.log(`[import] parsing ${rows.length} rows from ${abs}`);

  const result = canonicalCatalog.importRows(rows, { sourceTag: 'import_script', persist: true });
  console.log(`[import] accepted=${result.accepted} rejected=${result.rejected.length}`);

  for (const row of result.rejected) {
    console.log('[import] REJECTED', JSON.stringify(row));
  }

  let cached = 0;
  let cacheFailed = 0;
  for (const upd of result.updated) {
    const canon = canonicalCatalog.lookupByItemId(upd.itemId);
    if (!canon) continue;
    if (canon.imageAssetId) {
      const hit = await fishImageCache.ensureCachedAsset(canon.imageAssetId, {
        itemId: upd.itemId,
        baseFishName: canon.baseFishName,
      });
      if (hit.cached) cached += 1;
      else cacheFailed += 1;
    } else if (canon.imageUrl || canon.sourceUrl) {
      const hit = await fishImageCache.ensureCachedFromUrl(canon.imageUrl || canon.sourceUrl, {
        itemId: upd.itemId,
        baseFishName: canon.baseFishName,
      });
      if (hit.cached) cached += 1;
      else cacheFailed += 1;
    }
  }

  fishCatalog._reset();
  canonicalCatalog.rebuildFromAllSources({ persist: true });
  const audit = canonicalCatalog.getAudit();

  console.log(JSON.stringify({
    ok: true,
    accepted: result.accepted,
    rejected: result.rejected,
    imagesCached: cached,
    imagesCacheFailed: cacheFailed,
    audit: {
      totalEntries: audit.totalEntries,
      imageKnownCount: audit.imageKnownCount,
      imageMissingCount: audit.imageMissingCount,
      rarityKnownCount: audit.rarityKnownCount,
      rarityMissingCount: audit.rarityMissingCount,
      sourcesSearched: audit.sourcesSearched.map((s) => s.id || s),
      missingImageRows: audit.missingImageRows.slice(0, 20),
      missingRarityRows: audit.missingRarityRows.slice(0, 20),
    },
  }, null, 2));
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
