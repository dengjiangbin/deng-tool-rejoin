#!/usr/bin/env node
'use strict';

const base = process.argv[2] || 'http://127.0.0.1:8791';
const user = process.argv[3] || 'denghub2';

async function main() {
  const trackerRes = await fetch(`${base}/tracker`);
  const trackerHtml = await trackerRes.text();
  const buildMatch = trackerHtml.match(/BLOCKER10ZA[_A-Z0-9]+/g) || [];
  console.log('TRACKER_PAGE', {
    status: trackerRes.status,
    buildMarkers: [...new Set(buildMatch)],
    hasStonesSection: trackerHtml.includes('stones-section'),
    hasGameItemDbProofFn: trackerHtml.includes('buildPlayerDataGameItemDbProofHtml'),
    hasGlobalDbProofOnPublic: /Global DB proof/i.test(trackerHtml) && !trackerHtml.includes('debug=global'),
  });

  const debugRes = await fetch(`${base}/api/fishit-tracker/debug/${encodeURIComponent(user)}`);
  const debugText = await debugRes.text();
  let debug = null;
  try { debug = JSON.parse(debugText); } catch { /* ignore */ }
  if (!debug) {
    console.log('DEBUG_API', { status: debugRes.status, preview: debugText.slice(0, 400) });
    return;
  }
  const proof = debug.playerDataGameItemDbProof || {};
  console.log('DEBUG_API', {
    status: debugRes.status,
    user,
    renderBuild: debug.renderBuild,
    trackerBuild: debug.trackerBuild,
    inventorySource: debug.inventorySource,
    sourceTruth: debug.sourceTruth,
    fishItemsLen: Array.isArray(debug.fishItems) ? debug.fishItems.length : null,
    stoneItemsLen: Array.isArray(debug.stoneItems) ? debug.stoneItems.length : null,
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
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
