#!/usr/bin/env node
'use strict';
/**
 * BLOCKER10Z11 — Safe global FishTracker catalog reset + re-seed.
 *
 * Usage:
 *   node scripts/reset_fishit_global_catalog.js --dry-run
 *   node scripts/reset_fishit_global_catalog.js --confirm
 *   node scripts/reset_fishit_global_catalog.js --confirm --reset-images
 *   node scripts/reset_fishit_global_catalog.js --confirm --reset-sessions
 */

const path = require('path');
const fs = require('fs');

function parseArgs(argv) {
  const out = { dryRun: false, confirm: false, resetImages: false, resetSessions: false };
  for (let i = 2; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--dry-run') out.dryRun = true;
    else if (a === '--confirm') out.confirm = true;
    else if (a === '--reset-images') out.resetImages = true;
    else if (a === '--reset-sessions') out.resetSessions = true;
  }
  if (!out.dryRun && !out.confirm) out.dryRun = true;
  return out;
}

async function main() {
  const args = parseArgs(process.argv);
  const siteDir = path.join(__dirname, '..', 'site');
  process.chdir(siteDir);

  const globalCatalogService = require(path.join(siteDir, 'src', 'fishitGlobalCatalogService'));
  const dengBotCatalog = require(path.join(siteDir, 'src', 'fishitDengFishItBotCatalog'));

  const mode = args.confirm ? 'confirm' : 'dry-run';
  console.log('[fishit-reset] mode:', mode);
  console.log('  resetImages:', args.resetImages);
  console.log('  resetSessions:', args.resetSessions);

  const botMeta = dengBotCatalog.getCatalogMeta();
  console.log('  deng bot catalog entries:', botMeta.entryCount);
  console.log('  deng bot source:', botMeta.sourcePath);

  const proof = await globalCatalogService.resetGlobalCatalog({
    dryRun: args.dryRun,
    confirm: args.confirm,
    resetImages: args.resetImages,
    resetSessions: args.resetSessions,
  });

  console.log('[fishit-reset] proof:');
  console.log(JSON.stringify(proof, null, 2));

  if (args.dryRun) {
    console.log('[fishit-reset] dry-run complete — no files modified');
    process.exit(0);
  }

  const proofPath = path.join(siteDir, 'data', 'backups', `fishit_reset_proof_${Date.now()}.json`);
  fs.mkdirSync(path.dirname(proofPath), { recursive: true });
  fs.writeFileSync(proofPath, JSON.stringify(proof, null, 2));
  console.log('[fishit-reset] proof written:', proofPath);
  console.log('[fishit-reset] OK — catalog reset and re-seeded');
  process.exit(0);
}

main().catch((err) => {
  console.error('[fishit-reset] error:', err);
  process.exit(1);
});
