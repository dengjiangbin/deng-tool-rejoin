'use strict';

// BLOCKER B — prove the ingest-written store is readable by the site read path,
// and that the public-API mapping yields non-empty fish/stone/totem/leaderstats
// with per-instance mutation/weight.
//
// Usage: node site/scripts/prove_tracker_data_roundtrip.js --username <liveUser>
//
// Defaults env to the same paths deng-tool-site / deng-tracker-ingest use so the
// load path matches production exactly.

const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
process.env.FISHIT_SESSION_SHARDED = process.env.FISHIT_SESSION_SHARDED || '1';
process.env.FISHIT_LIVE_SESSIONS_DIR = process.env.FISHIT_LIVE_SESSIONS_DIR
  || path.join(ROOT, 'site', 'data', 'fishit_live_sessions');
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH
  || path.join('C:', 'Users', 'Administrator', 'Desktop', 'DENG Fish It', 'data', 'deng-fish-it.sqlite');

const args = process.argv.slice(2);
const uIdx = args.indexOf('--username');
const username = uIdx >= 0 ? args[uIdx + 1] : null;

const sessionStore = require('../src/fishitSessionStore');
const shardedStore = require('../src/fishitSessionStoreSharded');
const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const playerStatsStore = require('../src/fishitPlayerStats');

function laneCounts(data) {
  const pdFish = Array.isArray(data.playerDataFishItems) ? data.playerDataFishItems.length : 0;
  const pdStone = Array.isArray(data.playerDataStoneItems) ? data.playerDataStoneItems.length : 0;
  const pdTotem = Array.isArray(data.playerDataTotemItems) ? data.playerDataTotemItems.length : 0;
  return { pdFish, pdStone, pdTotem };
}

function instanceMetaSummary(publicFish) {
  let instWithMutation = 0;
  let instWithWeight = 0;
  let instNilMutationString = 0;
  let instMissingWeight = 0;
  let totalInstances = 0;
  for (const card of publicFish) {
    const insts = Array.isArray(card.ownedInstances) ? card.ownedInstances : [];
    for (const it of insts) {
      totalInstances += 1;
      const mut = it.mutationName || it.mutation || it.metadataMutation;
      if (mut && !['nil', 'null', 'none', ''].includes(String(mut).trim().toLowerCase())) instWithMutation += 1;
      if (typeof mut === 'string' && ['nil', 'null'].includes(mut.trim().toLowerCase())) instNilMutationString += 1;
      const w = it.weightKg ?? it.metadataWeightKg ?? it.weight;
      if (w != null && Number(w) > 0) instWithWeight += 1; else instMissingWeight += 1;
    }
  }
  return { totalInstances, instWithMutation, instWithWeight, instNilMutationString, instMissingWeight };
}

function main() {
  const liveTrackDB = {};
  const loadRes = sessionStore.loadIntoLiveTrackDB(liveTrackDB);
  const metrics = shardedStore.getShardedMetrics();
  console.log('=== STORE ===');
  console.log('mode=%s path=%s accountCount=%s loaded=%s', metrics.mode, metrics.path, metrics.accountCount, loadRes.loaded);
  console.log('FISHIT_DB_PATH=%s', process.env.FISHIT_DB_PATH);

  const keys = Object.keys(liveTrackDB).filter((k) => !k.startsWith('uid:'));
  console.log('liveTrackDB keys loaded=%s sample=%s', keys.length, keys.slice(0, 5).join(','));

  if (!username) {
    console.log('\n(no --username provided; pass one to see lane + public API mapping)');
    return;
  }
  const key = String(username).toLowerCase();
  const data = liveTrackDB[key];
  if (!data) {
    console.log('\n=== USER %s NOT FOUND in loaded store ===', username);
    console.log('inIndex=%s', !!(shardedStore.ensureIndexLoaded().accounts || {})[key]);
    return;
  }
  console.log('\n=== USER %s ===', username);
  console.log('userId=%s discordOwnerId=%s isOnline=%s trackerBuild=%s', data.userId, data.discordOwnerId, data.isOnline, data.trackerBuild);
  console.log('lastSeenAt=%s lastInventoryAt=%s', data.lastSeenAt, data.lastInventoryAt);
  console.log('snapshotComplete=%s inventoryReady=%s leaderstatsUploadOk=%s', data.snapshotComplete, data.inventoryReady, data.leaderstatsUploadOk);
  const lc = laneCounts(data);
  console.log('STORED lanes: playerDataFish=%s playerDataStone=%s playerDataTotem=%s', lc.pdFish, lc.pdStone, lc.pdTotem);
  console.log('STORED leaderstats playerStats=%s lastValidLeaderstats=%s',
    JSON.stringify(data.playerStats || null).slice(0, 160),
    JSON.stringify(data.lastValidLeaderstats || null).slice(0, 120));

  // Public API mapping (fish) directly from stored player-data rows.
  const fishRows = Array.isArray(data.playerDataFishItems) ? data.playerDataFishItems : [];
  const grouped = gameItemDbPublic.groupFishRows(fishRows);
  const publicFish = grouped.map((g) => gameItemDbPublic.mapToPublicFishCardItem(g));
  const stoneRows = Array.isArray(data.playerDataStoneItems) ? data.playerDataStoneItems : [];
  const groupedStones = gameItemDbPublic.groupStoneRows(stoneRows);
  console.log('\n=== PUBLIC API MAP ===');
  console.log('publicFishCards=%s publicStoneGroups=%s', publicFish.length, groupedStones.length);
  const meta = instanceMetaSummary(publicFish);
  console.log('ownedInstances total=%s withMutation=%s withWeight=%s nilMutationString=%s missingWeight=%s',
    meta.totalInstances, meta.instWithMutation, meta.instWithWeight, meta.instNilMutationString, meta.instMissingWeight);
  const sample = publicFish.find((c) => Array.isArray(c.ownedInstances) && c.ownedInstances.length);
  if (sample) {
    console.log('sample card name=%s rarity=%s qty=%s', sample.name || sample.baseFishName, sample.rarity, sample.quantity);
    for (const it of sample.ownedInstances.slice(0, 4)) {
      console.log('  inst uuid=%s mutation=%s mutationName=%s weightKg=%s mutPath=%s wtPath=%s',
        it.uuid, it.mutation, it.mutationName, it.weightKg, it.mutationSourcePath, it.weightSourcePath);
    }
  }
}

main();
