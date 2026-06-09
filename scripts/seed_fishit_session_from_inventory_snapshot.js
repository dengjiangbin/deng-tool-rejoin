#!/usr/bin/env node
'use strict';
/**
 * BLOCKER10Z17 — Seed FishTracker session from trusted user inventory snapshot.
 *
 * Usage:
 *   node scripts/seed_fishit_session_from_inventory_snapshot.js --session denghub2 --source user_snapshot_2026_06_09 --dry-run
 *   node scripts/seed_fishit_session_from_inventory_snapshot.js --session denghub2 --source user_snapshot_2026_06_09 --confirm
 */

const path = require('path');

function parseArgs(argv) {
  const out = {
    session: null,
    source: null,
    dryRun: false,
    confirm: false,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--session' && argv[i + 1]) { out.session = argv[++i]; continue; }
    if (a === '--source' && argv[i + 1]) { out.source = argv[++i]; continue; }
    if (a === '--dry-run') out.dryRun = true;
    else if (a === '--confirm') out.confirm = true;
  }
  if (!out.dryRun && !out.confirm) out.dryRun = true;
  return out;
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.session || !args.source) {
    console.error('Usage: node scripts/seed_fishit_session_from_inventory_snapshot.js --session SESSION --source SOURCE [--dry-run|--confirm]');
    process.exit(1);
  }

  const siteDir = path.join(__dirname, '..', 'site');
  process.chdir(siteDir);

  const snapshotRecovery = require(path.join(siteDir, 'src', 'fishitSnapshotRecovery'));

  const mode = args.confirm ? 'confirm' : 'dry-run';
  console.log('[fishit-snapshot-seed] mode:', mode);
  console.log('  session:', args.session);
  console.log('  source:', args.source);
  console.log('  available sources:', snapshotRecovery.listSnapshotSources().join(', '));

  const proof = snapshotRecovery.applySnapshotRecovery({
    sessionKey: args.session,
    sourceId: args.source,
    dryRun: args.dryRun,
    confirm: args.confirm,
  });

  console.log('[fishit-snapshot-seed] proof:');
  console.log(JSON.stringify(proof, null, 2));

  if (!proof.ok) {
    console.error('[fishit-snapshot-seed] FAILED:', proof.error);
    process.exit(1);
  }

  if (args.dryRun) {
    console.log('[fishit-snapshot-seed] dry-run complete — no files modified');
    process.exit(0);
  }

  console.log('[fishit-snapshot-seed] OK — recovery seeded');
  console.log('  backup:', proof.backup?.backupDir);
  console.log('  files modified:', proof.filesModified?.join(', '));
  console.log('  expected:', proof.totalExpectedFish, 'fish /', proof.totalExpectedTypes, 'types');
  process.exit(0);
}

main().catch((err) => {
  console.error('[fishit-snapshot-seed] error:', err);
  process.exit(1);
});
