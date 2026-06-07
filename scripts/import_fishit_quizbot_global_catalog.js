#!/usr/bin/env node
'use strict';
/**
 * BLOCKER10W — Import DENG Quiz Bot Fish It catalog into global SQLite DB.
 *
 * Usage:
 *   node scripts/import_fishit_quizbot_global_catalog.js --dry-run
 *   node scripts/import_fishit_quizbot_global_catalog.js --apply
 *   node scripts/import_fishit_quizbot_global_catalog.js --source "C:\...\fishit_bank.json" --assets "C:\...\FishItFish" --apply
 */

const path = require('path');
const fs = require('fs');

function parseArgs(argv) {
  const out = { dryRun: false, apply: false, source: null, assets: null };
  for (let i = 2; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--dry-run') out.dryRun = true;
    else if (a === '--apply') out.apply = true;
    else if (a === '--source' && argv[i + 1]) { out.source = argv[++i]; }
    else if (a === '--assets' && argv[i + 1]) { out.assets = argv[++i]; }
  }
  if (!out.dryRun && !out.apply) out.dryRun = true;
  return out;
}

async function main() {
  const args = parseArgs(process.argv);
  const siteDir = path.join(__dirname, '..', 'site');
  process.chdir(siteDir);

  const quizBotCatalog = require(path.join(siteDir, 'src', 'fishitQuizBotImageCatalog'));
  const globalCatalogService = require(path.join(siteDir, 'src', 'fishitGlobalCatalogService'));
  const globalDb = require(path.join(siteDir, 'src', 'fishitGlobalDb'));

  const bankPath = args.source || quizBotCatalog.BANK_PATH;
  const assetsDir = args.assets || quizBotCatalog.ASSETS_DIR;

  if (!fs.existsSync(bankPath)) {
    console.error('[fishit-quizbot-import] bank not found:', bankPath);
    process.exit(1);
  }

  const rows = JSON.parse(fs.readFileSync(bankPath, 'utf8'));
  const arr = Array.isArray(rows) ? rows : [];
  let missingAssets = 0;
  for (const entry of arr) {
    if (!entry?.localFile) { missingAssets += 1; continue; }
    const localFile = path.join(assetsDir, entry.localFile);
    if (!fs.existsSync(localFile)) missingAssets += 1;
  }

  const panther = arr.find((e) => String(e.name).toLowerCase() === 'panther eel');
  const squid = arr.find((e) => String(e.name).toLowerCase() === 'giant squid');
  const piranha = arr.filter((e) => String(e.name).toLowerCase() === 'freshwater piranha');

  console.log('[fishit-quizbot-import] mode:', args.apply ? 'apply' : 'dry-run');
  console.log('  source:', bankPath);
  console.log('  assets:', assetsDir);
  console.log('  bank rows:', arr.length);
  console.log('  missing asset files:', missingAssets);
  console.log('  Panther Eel in bank:', panther ? panther.id : 'NOT FOUND');
  console.log('  Giant Squid in bank:', squid ? squid.id : 'NOT FOUND');
  console.log('  Freshwater Piranha entries:', piranha.length);

  if (args.dryRun) {
    console.log('[fishit-quizbot-import] dry-run complete — no DB changes');
    process.exit(0);
  }

  const result = await globalCatalogService.importQuizBotSeed({ bankPath, assetsDir });
  if (!result.ok) {
    console.error('[fishit-quizbot-import] FAILED:', result);
    process.exit(1);
  }

  const stats = globalDb.getStats();
  const peSpecies = globalDb.findSpeciesByAliases(['Panther Eel']);
  const gsSpecies = globalDb.findSpeciesByAliases(['Giant Squid']);
  const fpSpecies = globalDb.findSpeciesByAliases(['Freshwater Piranha']);

  console.log('[fishit-quizbot-import] OK');
  console.log(`  species imported: ${result.speciesImported}`);
  console.log(`  images imported:  ${result.imagesImported}`);
  console.log(`  skipped rows:     ${result.skipped}`);
  console.log(`  db species:       ${stats.speciesCount}`);
  console.log(`  db images:        ${stats.imageAssetCount}`);
  console.log(`  Panther Eel DB:   ${peSpecies?.species?.canonical_name || 'missing'} image=${!!peSpecies?.species?.cached_image_url}`);
  console.log(`  Giant Squid DB:   ${gsSpecies?.species?.canonical_name || 'missing'} rarity=${gsSpecies?.species?.rarity || 'null'}`);
  console.log(`  Freshwater Piranha DB: rarity=${fpSpecies?.species?.rarity || 'null'}`);
  process.exit(0);
}

main().catch((err) => {
  console.error('[fishit-quizbot-import] error:', err);
  process.exit(1);
});
