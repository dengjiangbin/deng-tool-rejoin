#!/usr/bin/env node
'use strict';

const base = process.argv[2] || 'http://127.0.0.1:8791';
const user = process.argv[3] || 'denghub2';

const failures = [];

function fail(msg) {
  failures.push(msg);
  console.error('FAIL', msg);
}

async function main() {
  const trackerRes = await fetch(`${base}/tracker`);
  const trackerHtml = await trackerRes.text();
  const buildMatch = trackerHtml.match(/BLOCKER10Z[ABCD][_A-Z0-9]+/g) || [];
  console.log('TRACKER_PAGE', {
    status: trackerRes.status,
    buildMarkers: [...new Set(buildMatch)],
    hasStonesSection: trackerHtml.includes('stones-section'),
    hasStoneAssetRoute: trackerHtml.includes('assets/stones/') || trackerHtml.includes('publicRarity'),
    hasGameItemDbProofFn: trackerHtml.includes('buildPlayerDataGameItemDbProofHtml'),
    hasGlobalDbProofOnPublic: /Global DB proof/i.test(trackerHtml) && !trackerHtml.includes('debug=global'),
  });

  const debugRes = await fetch(`${base}/api/fishit-tracker/debug/${encodeURIComponent(user)}`);
  const debugText = await debugRes.text();
  let debug = null;
  try { debug = JSON.parse(debugText); } catch { /* ignore */ }
  if (!debug) {
    fail(`debug API did not return JSON (status=${debugRes.status})`);
    console.log('DEBUG_API', { status: debugRes.status, preview: debugText.slice(0, 400) });
    process.exit(1);
  }

  const proof = debug.playerDataGameItemDbProof || {};
  console.log('DEBUG_API', {
    status: debugRes.status,
    user,
    renderBuild: debug.renderBuild,
    trackerBuild: debug.trackerBuild,
    activationState: debug.activationState,
    inventorySource: debug.inventorySource,
    sourceTruth: debug.sourceTruth,
    fishItemsLen: Array.isArray(debug.fishItems) ? debug.fishItems.length : null,
    stoneItemsLen: Array.isArray(debug.stoneItems) ? debug.stoneItems.length : null,
    missingPublicRarityCount: debug.missingPublicRarityCount,
    publicCounts: debug.publicCounts,
    playerDataGameItemDbProof: {
      enabled: proof.enabled,
      build: proof.build,
      inventorySource: proof.inventorySource,
      gameItemDbBuilt: proof.gameItemDbBuilt,
      gameItemDbCount: proof.gameItemDbCount,
      uploadedFishCount: proof.uploadedFishCount,
      uploadedStoneCount: proof.uploadedStoneCount,
      fishIconResolvedCount: proof.fishIconResolvedCount,
      globalDbUsedForPublicIdentity: proof.globalDbUsedForPublicIdentity,
      sampleFish: proof.sampleFish,
      sampleStones: proof.sampleStones,
    },
  });

  if (debug.inventorySource == null) {
    fail('inventorySource is null — playerdata_gameitemdb payload not active');
  } else if (debug.inventorySource !== 'playerdata_gameitemdb') {
    fail(`inventorySource=${debug.inventorySource} expected playerdata_gameitemdb`);
  }

  if (!proof || proof.gameItemDbBuilt !== true) {
    fail('playerDataGameItemDbProof.gameItemDbBuilt is not true');
  }

  if (debug.sourceTruth?.globalDbUsedForPublicIdentity !== false) {
    fail('sourceTruth.globalDbUsedForPublicIdentity is not false');
  }

  if (debug.missingPublicRarityCount != null && debug.missingPublicRarityCount !== 0) {
    fail(`missingPublicRarityCount=${debug.missingPublicRarityCount} expected 0`);
  }

  const fishRows = Array.isArray(debug.fishItems) ? debug.fishItems : [];
  const stoneRows = Array.isArray(debug.stoneItems) ? debug.stoneItems : [];
  const activeRows = [...fishRows, ...stoneRows];

  const rarityExpect = {
    'Panther Eel': 'Secret',
    'Skeleton Angler Fish': 'Epic',
    'Red Goatfish': 'Uncommon',
    'Zebra Snakehead': 'Uncommon',
  };
  for (const [name, expected] of Object.entries(rarityExpect)) {
    const row = fishRows.find((f) => f.name === name || f.baseFishName === name);
    if (row && row.rarity !== expected) {
      fail(`${name} rarity=${row.rarity} expected ${expected}`);
    }
    if (row) {
      console.log('RARITY_CHECK', { name, rarity: row.rarity, raritySource: row.raritySource, tier: row.tier });
    }
  }

  if (stoneRows.length > 0) {
    const firstStone = stoneRows[0];
    console.log('STONE_IMAGE_CHECK', {
      itemId: firstStone.itemId,
      name: firstStone.name,
      imageSource: firstStone.imageSource,
      imageUrl: firstStone.imageUrl,
      imageUrlPresent: firstStone.imageUrlPresent,
    });
    if (firstStone.imageSource !== 'stone_manual_asset') {
      fail(`stoneItems[0].imageSource=${firstStone.imageSource} expected stone_manual_asset`);
    }
  }

  for (const row of activeRows) {
    if (row.imageSource === 'global_db') {
      fail(`active public row uses global_db image: ${row.name || row.itemId}`);
    }
    if (row.dataSource === 'global_db') {
      fail(`active public row uses global_db identity: ${row.name || row.itemId}`);
    }
    if (row.dataRaritySource === 'global_db') {
      fail(`active public row uses global_db rarity: ${row.name || row.itemId}`);
    }
  }

  const item10AsFish = fishRows.some((f) => String(f.itemId) === '10');
  if (item10AsFish) {
    fail('itemId 10 appears as fish — must be stone');
  }

  const backpackRes = await fetch(`${base}/api/fishit-tracker/get-backpack/${encodeURIComponent(user)}`);
  if (backpackRes.ok) {
    const backpack = await backpackRes.json();
    console.log('GET_BACKPACK', {
      inventorySource: backpack.inventorySource,
      activationState: backpack.activationState,
      stoneItemsLen: Array.isArray(backpack.stoneItems) ? backpack.stoneItems.length : null,
    });
  }

  if (failures.length) {
    console.error(`\n${failures.length} live proof failure(s):`);
    failures.forEach((f, i) => console.error(`${i + 1}. ${f}`));
    process.exit(1);
  }

  console.log('LIVE_PROOF_OK');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
