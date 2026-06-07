#!/usr/bin/env node
'use strict';
/**
 * BLOCKER10V — Import DENG Quiz Bot Fish It catalog into global SQLite DB.
 *
 * Usage: node scripts/import_quiz_bot_to_global_db.js
 */

const path = require('path');

const siteDir = path.join(__dirname, '..', 'site');
process.chdir(siteDir);

const globalCatalogService = require(path.join(siteDir, 'src', 'fishitGlobalCatalogService'));

async function main() {
  console.log('[fishit-global-import] Starting Quiz Bot seed import...');
  const result = await globalCatalogService.importQuizBotSeed();
  if (!result.ok) {
    console.error('[fishit-global-import] FAILED:', result);
    process.exit(1);
  }
  console.log('[fishit-global-import] OK');
  console.log(`  species imported: ${result.speciesImported}`);
  console.log(`  images imported:  ${result.imagesImported}`);
  console.log(`  bank rows:        ${result.totalBankRows}`);
  console.log(`  seed source:      ${result.seedSource}`);
  console.log(`  db stats:         ${JSON.stringify(result.stats)}`);
  process.exit(0);
}

main().catch((err) => {
  console.error('[fishit-global-import] error:', err);
  process.exit(1);
});
