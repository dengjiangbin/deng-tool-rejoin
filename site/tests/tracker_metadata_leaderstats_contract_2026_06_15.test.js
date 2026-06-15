'use strict';

// BLOCKER 8 — end-to-end contract for the 2026-06-15 live metadata fix.
// Proves the two real root causes are fixed:
//   1. The production fast-path compactor (stripHeavyUploadFields) no longer
//      drops per-instance mutation/weight ("Weight unknown" root cause).
//   2. The leaderstats trust gate accepts the current public build (leaderstats
//      missing root cause) and partial heartbeats never erase stored values.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const compact = require('../src/fishitTrackerCompactUpload');
const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const playerStats = require('../src/fishitPlayerStats');
const leaderstatsUpload = require('../src/fishitLeaderstatsUpload');
const { PRODUCTION_TRACKER_BUILD } = require('../src/fishitTrackerBuild');

const RAW_TRACKER_LUA = path.join(
  'C:', 'Users', 'Administrator', 'Desktop', 'DENG PRIVATE SOURCE', 'fishtracker', 'tracker.lua',
);
const hasRaw = fs.existsSync(RAW_TRACKER_LUA);
const testIfRaw = hasRaw ? test : test.skip;

// A Lua-emitted fish row carrying the real per-instance metadata.
function luaFishRow(over) {
  return {
    kind: 'fish', type: 'Fish', itemId: '901', name: 'King Crab', baseName: 'King Crab',
    quantity: 1, uuid: 'uuid-' + Math.random().toString(36).slice(2, 8),
    icon: 'rbxassetid://123', tier: 6, rarity: 'Mythic',
    source: 'playerdata_gameitemdb', identityVerified: true,
    ...over,
  };
}

test('BLOCKER1+3 compactor preserves per-instance mutation + weight aliases (fish)', () => {
  const row = compact.compactInventoryRow(luaFishRow({
    mutation: 'Gold', mutationName: 'Gold', metadataMutation: 'Gold',
    mutationSourcePath: 'Metadata.Mutation',
    weightKg: 12.34, metadataWeightKg: 12.34, weight: 12.34,
    weightSourcePath: 'Metadata.Weight',
  }), 'fish');
  assert.equal(row.mutation, 'Gold');
  assert.equal(row.mutationName, 'Gold');
  assert.equal(row.weightKg, 12.34);
  assert.equal(row.mutationSourcePath, 'Metadata.Mutation');
  assert.equal(row.weightSourcePath, 'Metadata.Weight');
});

test('BLOCKER1 stripHeavyUploadFields (production fast path) keeps mutation + weight', () => {
  const body = {
    type: 'inventory_snapshot',
    fishItems: [luaFishRow({ mutation: 'Gold', weightKg: 9.5 })],
    stoneItems: [],
    totemItems: [],
  };
  const stripped = compact.stripHeavyUploadFields(body, { isDebug: false });
  assert.equal(stripped.fishItems.length, 1);
  assert.equal(stripped.fishItems[0].mutation, 'Gold');
  assert.equal(stripped.fishItems[0].weightKg, 9.5);
});

test('BLOCKER4 backend preserves raw mutation/weight into public ownedInstances (7 King Crab)', () => {
  // 7 same-species instances: 2 Gold (different weights), 1 Shiny, 4 normals.
  const rows = [
    luaFishRow({ mutation: 'Gold', weightKg: 18.2 }),
    luaFishRow({ mutation: 'Gold', weightKg: 11.7 }),
    luaFishRow({ mutation: 'Shiny', weightKg: 7.4 }),
    luaFishRow({ weightKg: 5.1 }),
    luaFishRow({ weightKg: 4.9 }),
    luaFishRow({ weightKg: 6.3 }),
    luaFishRow({ weightKg: 3.0 }),
  ].map((r) => compact.compactInventoryRow(r, 'fish'));

  const grouped = gameItemDbPublic.groupFishRows(rows);
  assert.equal(grouped.length, 1, 'one aggregated King Crab card');
  const card = gameItemDbPublic.mapToPublicFishCardItem(grouped[0]);
  assert.equal(card.ownedInstances.length, 7, 'seven owned instances preserved');

  const golds = card.ownedInstances.filter((i) => String(i.mutation || '').toLowerCase() === 'gold');
  assert.equal(golds.length, 2, 'exactly two Gold instances');
  assert.deepEqual(golds.map((g) => g.weightKg).sort((a, b) => a - b), [11.7, 18.2]);

  const shiny = card.ownedInstances.filter((i) => String(i.mutation || '').toLowerCase() === 'shiny');
  assert.equal(shiny.length, 1, 'one Shiny instance');

  // No instance with a real weight should be "unknown" (null).
  const unknownWeights = card.ownedInstances.filter((i) => i.weightKg == null);
  assert.equal(unknownWeights.length, 0, 'all instances have a numeric weight');

  // mutationName alias is also exposed for the frontend.
  assert.ok(card.ownedInstances.every((i) => 'mutationName' in i));
});

test('BLOCKER4 nested Metadata.Mutation/Weight still extracted by backend', () => {
  const row = { kind: 'fish', type: 'Fish', itemId: '5', name: 'Tuna', baseName: 'Tuna',
    Metadata: { Mutation: 'Gold', Weight: 22.5 } };
  const norm = gameItemDbPublic.normaliseUploadRow(row);
  assert.equal(norm.instanceMutation, 'Gold');
  assert.equal(norm.weightKg, 22.5);
});

test('BLOCKER6 leaderstats trust gate accepts the current public build', () => {
  assert.equal(playerStats.isTrustedPlayerStatsBuild(PRODUCTION_TRACKER_BUILD), true);
  assert.equal(playerStats.isTrustedPlayerStatsBuild('METADATA_PROBE_DEEP_SCAN_2026_06_15'), true);
  assert.equal(playerStats.isTrustedPlayerStatsBuild('INSTANCE_MUTATION_WEIGHT_DETAIL_2026_06_15'), true);
});

test('BLOCKER6 full leaderstats upload stored; partial heartbeat preserves it', () => {
  const now = new Date().toISOString();
  const body = {
    clientOrigin: 'roblox_tracker',
    trackerBuild: PRODUCTION_TRACKER_BUILD,
    leaderstatsReady: true,
    playerStats: { coins: 80997461, totalCaught: 143186, source: 'leaderstats', build: PRODUCTION_TRACKER_BUILD },
  };
  const stored = leaderstatsUpload.applyLeaderstatsUploadFields(null, body, now);
  assert.equal(stored.leaderstatsUploadOk, true, 'leaderstats accepted + stored');
  assert.ok(stored.lastValidLeaderstats, 'lastValidLeaderstats populated');
  assert.equal(stored.lastValidLeaderstats.coins, 80997461);

  // A later heartbeat with NO leaderstats must not erase the stored values.
  const existing = { ...stored };
  const afterHb = leaderstatsUpload.applyLeaderstatsUploadFields(existing, { trackerBuild: PRODUCTION_TRACKER_BUILD }, new Date().toISOString(), { isHeartbeat: true });
  assert.ok(afterHb.lastValidLeaderstats, 'heartbeat preserves lastValidLeaderstats');
  assert.equal(afterHb.lastValidLeaderstats.coins, 80997461);
});

testIfRaw('BLOCKER1+2+3 Lua ships bounded probe + deep search + alias emit + new marker', () => {
  const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
  assert.match(lua, /TRACKER_BUILD = "METADATA_PROBE_DEEP_SCAN_2026_06_15"/);
  assert.match(lua, /function LiveSafe\.describeKeys/);
  assert.match(lua, /function LiveSafe\.findFirstKeyDeep/);
  assert.match(lua, /function LiveSafe\.debugMetadataProbe/);
  assert.match(lua, /function LiveSafe\.readItemMutationWithPath/);
  assert.match(lua, /function LiveSafe\.readItemWeightKgWithPath/);
  assert.match(lua, /INVENTORY_INSTANCE_METADATA_EMIT/);
  assert.match(lua, /out\.mutationName = row\.mutationName/);
  assert.match(lua, /out\.metadataWeightKg = w/);
  // deep search must be bounded + nil-safe (no late parseWeight call in helpers)
  assert.match(lua, /maxDepth or 4/);
});
