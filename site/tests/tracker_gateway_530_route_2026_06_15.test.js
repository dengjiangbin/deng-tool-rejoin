'use strict';

// BLOCKER H — regression protection for the 2026-06-15 Cloudflare 530 incident.
// Root cause was the Windows `Cloudflared` tunnel connector service being
// stopped, so the public origin returned Cloudflare 530 text/html instead of
// backend JSON. These tests lock in:
//   1. Lua gateway HTML/5xx detection (UPLOAD_HTML_GATEWAY_ERROR) so a future
//      tunnel outage is clearly diagnosed and never counted as a good upload.
//   2. The new build marker is production + allowlisted + trusted everywhere.
//   3. Lane-merge safety: heartbeat/status-only and inventory-only uploads can
//      never erase stored leaderstats; zero-fish partials preserve last-good fish.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const { PRODUCTION_TRACKER_BUILD, isAllowedTrackerBuild } = require('../src/fishitTrackerBuild');
const playerStats = require('../src/fishitPlayerStats');
const leaderstatsUpload = require('../src/fishitLeaderstatsUpload');
const compact = require('../src/fishitTrackerCompactUpload');
const partialSnapshot = require('../src/fishitPartialSnapshot');

const RAW_TRACKER_LUA = path.join(
  'C:', 'Users', 'Administrator', 'Desktop', 'DENG PRIVATE SOURCE', 'fishtracker', 'tracker.lua',
);
const EJS_SOURCE = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const hasRaw = fs.existsSync(RAW_TRACKER_LUA);
const testIfRaw = hasRaw ? test : test.skip;

const NEW_MARKER = 'UPLOAD_HTML_530_GATEWAY_DIAG_2026_06_15';

test('BLOCKER-D new gateway-diag marker is the production build + allowlisted', () => {
  assert.equal(PRODUCTION_TRACKER_BUILD, NEW_MARKER);
  assert.equal(isAllowedTrackerBuild(NEW_MARKER), true);
  // Previous marker stays accepted during client rollout.
  assert.equal(isAllowedTrackerBuild('METADATA_PROBE_DEEP_SCAN_2026_06_15'), true);
});

test('BLOCKER-G leaderstats trust gate accepts the new marker + allowlisted builds', () => {
  assert.equal(playerStats.isTrustedPlayerStatsBuild(NEW_MARKER), true);
  assert.equal(playerStats.isTrustedPlayerStatsBuild('METADATA_PROBE_DEEP_SCAN_2026_06_15'), true);
  assert.equal(playerStats.isTrustedPlayerStatsBuild('UPLOAD_COMPACT_FAST_PATH_2026_06_13'), true);
});

test('frontend EJS trusts the new gateway-diag marker prefix', () => {
  const ejs = fs.readFileSync(EJS_SOURCE, 'utf8');
  assert.match(ejs, /UPLOAD_HTML_530_GATEWAY_DIAG/);
});

testIfRaw('BLOCKER-C Lua detects gateway HTML / 5xx and logs UPLOAD_HTML_GATEWAY_ERROR', () => {
  const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
  // helper + log function present
  assert.match(lua, /function HttpDash\.isGatewayHtmlError/);
  assert.match(lua, /function HttpDash\.logGatewayHtmlError/);
  assert.match(lua, /UPLOAD_HTML_GATEWAY_ERROR lane=%s status=%s host=%s contentType=%s/);
  // covers 530 and the 52x band
  assert.match(lua, /c == 530 or \(c >= 520 and c <= 526\)/);
  // text/html content-type detection
  assert.match(lua, /text\/html/);
  // bounded safe body prefix (max 120 chars)
  assert.match(lua, /Body:gsub\("%s\+", " "\):sub\(1, 120\)/);
  // gateway HTML must never be marked a success
  assert.match(lua, /if HttpDash\.isGatewayHtmlError\(result\) then return false end/);
  // build marker bumped for this diagnostic change
  assert.match(lua, /TRACKER_BUILD = "UPLOAD_HTML_530_GATEWAY_DIAG_2026_06_15"/);
});

test('BLOCKER-H metadata: compactor preserves per-instance mutation + weight aliases', () => {
  const row = compact.compactInventoryRow({
    kind: 'fish', type: 'Fish', itemId: '7', name: 'Tuna', baseName: 'Tuna', quantity: 1,
    mutation: 'Gold', mutationName: 'Gold', metadataMutation: 'Gold', mutationSourcePath: 'Metadata.Mutation',
    weightKg: 14.2, metadataWeightKg: 14.2, weight: 14.2, weightSourcePath: 'Metadata.Weight',
    source: 'playerdata_gameitemdb',
  }, 'fish');
  assert.equal(row.mutation, 'Gold');
  assert.equal(row.weightKg, 14.2);
  assert.equal(row.mutationSourcePath, 'Metadata.Mutation');
  assert.equal(row.weightSourcePath, 'Metadata.Weight');
});

test('BLOCKER-H lane merge: leaderstats-only stored, then inventory-only (no stats) preserves it', () => {
  const now = new Date().toISOString();
  const stored = leaderstatsUpload.applyLeaderstatsUploadFields(null, {
    clientOrigin: 'roblox_tracker', trackerBuild: PRODUCTION_TRACKER_BUILD, leaderstatsReady: true,
    playerStats: { coins: 5000, totalCaught: 321, source: 'leaderstats', build: PRODUCTION_TRACKER_BUILD },
  }, now);
  assert.equal(stored.leaderstatsUploadOk, true);
  assert.equal(stored.lastValidLeaderstats.coins, 5000);

  // Inventory-only upload arriving with NO playerStats must not erase leaderstats.
  const afterInv = leaderstatsUpload.applyLeaderstatsUploadFields(
    { ...stored }, { clientOrigin: 'roblox_tracker', trackerBuild: PRODUCTION_TRACKER_BUILD, type: 'inventory_snapshot' }, new Date().toISOString(),
  );
  assert.ok(afterInv.lastValidLeaderstats, 'leaderstats preserved through inventory-only upload');
  assert.equal(afterInv.lastValidLeaderstats.coins, 5000);
  assert.ok(afterInv.playerStats, 'stored playerStats preserved');
  assert.equal(afterInv.playerStats.coins, 5000);
});

test('BLOCKER-H lane merge: heartbeat/status-only cannot erase leaderstats', () => {
  const now = new Date().toISOString();
  const stored = leaderstatsUpload.applyLeaderstatsUploadFields(null, {
    clientOrigin: 'roblox_tracker', trackerBuild: PRODUCTION_TRACKER_BUILD, leaderstatsReady: true,
    playerStats: { coins: 777, totalCaught: 9, source: 'leaderstats', build: PRODUCTION_TRACKER_BUILD },
  }, now);
  const afterHb = leaderstatsUpload.applyLeaderstatsUploadFields(
    { ...stored }, { trackerBuild: PRODUCTION_TRACKER_BUILD, type: 'tracker_status' }, new Date().toISOString(), { isHeartbeat: true },
  );
  assert.ok(afterHb.lastValidLeaderstats, 'heartbeat preserves leaderstats');
  assert.equal(afterHb.lastValidLeaderstats.coins, 777);
});

test('BLOCKER-H lane merge: zero-fish partial preserves last-good fish (status-only cannot erase inventory)', () => {
  const existing = {
    lastGoodFishItems: [{ category: 'fish', name: 'King Crab', itemId: '901' }],
    lastGoodPublicFishCount: 1,
  };
  const partialInfo = partialSnapshot.detectPartialZeroFishSnapshot({
    ps: { accepted: 0, fish: 0 },
    cleanItems: [],
    existing,
    priorPublicFishCount: 1,
  });
  assert.equal(partialInfo.isPartial, true, 'zero-fish upload with prior good fish is partial');

  const preserved = partialSnapshot.applyPartialSnapshotPreservation({
    cleanItems: [], rawItems: [], inventory: null, existing, partialInfo,
  });
  assert.equal(preserved.cleanItems.length, 1, 'last-good fish preserved, not erased');
  assert.equal(preserved.cleanItems[0].name, 'King Crab');
});
