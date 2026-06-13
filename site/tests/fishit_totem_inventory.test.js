'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.FISHIT_TEST_FIXTURE = '1';

const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const gate = require('../src/trackerConcurrencyGate');
const trackerRouter = require('../src/fishitTrackerRoutes');
const {
  MINIMUM_TRACKER_BUILD,
  ALLOWED_TRACKER_CHANNEL,
  ALLOWED_TRACKER_RAW_URL,
} = require('../src/fishitTrackerChannelEnforcement');
const { resolveRawTrackerSourcePath } = require('../../scripts/trackerRawSourcePath');

const ADMIN_TOKEN = 'totem-test-admin-token';

function totemRow(name, itemId, qty = 1, overrides = {}) {
  return {
    kind: 'totem',
    itemId: String(itemId),
    uuid: `uuid-${itemId}`,
    name,
    quantity: qty,
    type: 'Totem',
    icon: 'rbxassetid://1234567890123',
    source: 'playerdata_gameitemdb',
    identityVerified: true,
    ...overrides,
  };
}

function stoneRow(type, itemId, qty = 1) {
  return {
    kind: 'stone',
    itemId: String(itemId),
    name: `${type} Enchant Stone`,
    stoneType: type,
    quantity: qty,
    source: 'playerdata_gameitemdb',
    identityVerified: true,
  };
}

function buildUploadBody(username, extras = {}) {
  return {
    username,
    userId: 9001,
    isOnline: true,
    type: 'inventory_snapshot',
    trackerBuild: MINIMUM_TRACKER_BUILD,
    trackerChannel: ALLOWED_TRACKER_CHANNEL,
    scriptSource: ALLOWED_TRACKER_RAW_URL,
    trackerClientProof: {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      trackerChannel: ALLOWED_TRACKER_CHANNEL,
      scriptSource: ALLOWED_TRACKER_RAW_URL,
    },
    inventorySource: 'playerdata_gameitemdb',
    scanCompleted: true,
    replionReady: true,
    leaderstatsReady: true,
    fishScanReady: true,
    stoneScanReady: true,
    fishItems: [],
    stoneItems: [],
    totemItems: [],
    ...extras,
  };
}

describe('FishIt totem inventory support', () => {
  test('classifies Mutation Totem and Shiny Totem by name', () => {
    assert.equal(gameItemDbPublic.isTotemRow(totemRow('Mutation Totem', '501')), true);
    assert.equal(gameItemDbPublic.isTotemRow(totemRow('Shiny Totem', '502')), true);
    assert.equal(gameItemDbPublic.isTotemRow(totemRow('Future Lucky Totem', '777')), true);
    assert.equal(gameItemDbPublic.isTotemRow(stoneRow('Normal', '10')), false);
  });

  test('normaliseUploadRow keeps totems out of stone rows', () => {
    const rows = gameItemDbPublic.normaliseUploadRows([
      totemRow('Mutation Totem', '501', 3),
      stoneRow('Normal', '10', 2),
      totemRow('Shiny Totem', '502', 1),
    ]);
    const totems = rows.filter((r) => r.kind === 'totem');
    const stones = rows.filter((r) => r.kind === 'stone');
    assert.equal(totems.length, 2);
    assert.equal(stones.length, 1);
    assert.equal(totems[0].type, 'Totem');
    assert.equal(totems[0].quantity, 3);
  });

  test('groupTotemRows sums unstacked rows by name/itemId', () => {
    const grouped = gameItemDbPublic.groupTotemRows([
      totemRow('Mutation Totem', '501', 1, { uuid: 'a' }),
      totemRow('Mutation Totem', '501', 1, { uuid: 'b' }),
      totemRow('Shiny Totem', '502', 1, { uuid: 'c' }),
    ]);
    assert.equal(grouped.length, 2);
    const mutation = grouped.find((r) => r.name === 'Mutation Totem');
    assert.equal(mutation.quantity, 2);
    assert.equal(mutation.rowCount, 2);
  });

  test('loader scan source detects totem path + compact production build', () => {
    const luaPath = resolveRawTrackerSourcePath({ root: path.join(__dirname, '..', '..') });
    assert.ok(luaPath && fs.existsSync(luaPath), 'canonical private tracker source must exist');
    const lua = fs.readFileSync(luaPath, 'utf8');
    assert.match(lua, /totemItemRows/);
    assert.match(lua, /LiveSafe\.aggregateTotemItemsByName/);
    assert.match(lua, /LiveSafe\.resolveRowEffectiveQuantity/);
    assert.match(lua, /inventoryItemClassificationDebug/);
    assert.match(lua, /totemPathAudit/);
    assert.match(lua, /gameItemDbTotemAudit/);
    assert.match(lua, /nonFishNonStoneItemGroups/);
    assert.match(lua, /auditReplionInventoryPaths/);
    assert.match(lua, /UNRESOLVED_GROUP id=%s candidateNames=%s rows=%d quantitySum=%d effectiveQty=%d/);
    assert.match(lua, /debugAuditUpload ~= true then return/);
    assert.match(lua, /UPLOAD_COMPACT_FAST_PATH_2026_06_13/);
    assert.doesNotMatch(lua, /promoteTotemEvidenceFromUnresolvedGroups/);
    assert.match(lua, /PLAYERDATA_GAMEITEMDB_UPLOAD_OK %s status=%s fish=%d stones=%d totems=%d totemQty=%d/);
    assert.match(lua, /TOTEM_AUDIT_PAYLOAD paths=%d matches=%d nonFishGroups=%d classificationDebug=%s payloadHasTotemPathAudit=%s/);
    assert.match(lua, /UPLOAD_PAYLOAD_KEYS hasTotemPathAudit=%s hasGameItemDbTotemAudit=%s hasInventoryItemClassificationDebug=%s hasNonFishGroups=%s/);
    assert.match(lua, /LiveSafe\.attachGameItemScanAuditFields/);
    assert.match(lua, /Inventory\.Totems/);
    assert.match(lua, /LiveSafe\.processTotemInventoryPath/);
    assert.match(lua, /TOTEM_PATH_FOUND path=%s rows=%d effectiveQty=%d notificationQty=%s/);
    assert.match(lua, /totemInventoryPathProof/);
    assert.match(lua, /readInventoryNotificationsTotemsQty/);
    assert.match(lua, /;\(function\(\)/);
    assert.match(lua, /function ensureUploadRuntimeState/);
    assert.match(lua, /^end\)\(\)\s*$/m);
  });

  test('public dist build is protected wrapper not raw source', () => {
    const distPath = path.join(__dirname, '..', '..', 'dist', 'tracker.lua');
    assert.ok(fs.existsSync(distPath), 'dist/tracker.lua must exist after build');
    const dist = fs.readFileSync(distPath, 'utf8');
    assert.match(dist, /UPLOAD_COMPACT_FAST_PATH_2026_06_13/);
    assert.match(dist, /local __B=\[\[/);
    assert.doesNotMatch(dist, /^\(function\(\)/m);
    const m = dist.match(/local __B=\[\[([\s\S]*?)\]\]\nlocal __A=/);
    assert.ok(m, 'dist payload must be base64 wrapped');
    const decoded = Buffer.from(m[1], 'base64').toString('utf8');
    assert.match(decoded, /;\(function\(\)/);
    assert.match(decoded, /totems=%d totemQty=%d/);
    assert.match(decoded, /TOTEM_AUDIT_PAYLOAD paths=%d matches=%d nonFishGroups=%d/);
    assert.match(decoded, /LiveSafe\.attachGameItemScanAuditFields/);
  });

  test('frontend source exposes Totems section headers without Item Grid', () => {
    const source = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'),
      'utf8',
    );
    assert.doesNotMatch(source, /Item Grid/);
    assert.match(source, /Enchant Stones \(\$\{formatQuantity\(stoneTotal\)\}\)/);
    assert.match(source, /Totems \(\$\{formatQuantity\(totemTotal\)\}\)/);
    assert.match(source, /totems-section__title/);
    assert.match(source, /function getPublicTotemItems/);
    assert.match(source, /function patchItemGrid/);
    assert.match(source, /totemCardKey/);
  });
});

describe('FishIt totem upload API integration', () => {
  function makeTrackerApp() {
    gate._resetForTests();
    const app = express();
    app.use(express.json({ limit: '2mb' }));
    app.use(trackerRouter);
    return app;
  }

  test('backend persists totemItems and returns them on latest API', async () => {
    const app = makeTrackerApp();
    const username = 'TotemUser1';
    const body = buildUploadBody(username, {
      debugUpload: true,
      fishItems: [{
        kind: 'fish', itemId: '70', name: 'Clownfish', baseName: 'Clownfish',
        quantity: 1, tier: 1, rarity: 'Common', type: 'Fish', source: 'playerdata_gameitemdb',
        identityVerified: true,
      }],
      stoneItems: [stoneRow('Normal', '10', 2)],
      totemItems: [
        totemRow('Mutation Totem', '501', 3),
        totemRow('Shiny Totem', '502', 1),
      ],
      inventoryItemClassificationDebug: {
        totalRows: 94,
        fishRows: 1,
        stoneRows: 1,
        totemRows: 2,
        totemTypeCount: 2,
        totemEffectiveQty: 4,
        unresolvedRows: 40,
        unresolvedGroups: [{
          id: 267,
          rows: 40,
          quantitySum: 40,
          effectiveQuantity: 40,
          candidateNames: ['Diamond Artifact'],
          rawKeys: ['Id', 'Metadata', 'UUID'],
        }],
        totemCandidateGroups: [],
        classificationSamples: [],
      },
    });

    const upload = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(body)
      .expect(200);
    assert.equal(upload.body.ok, true);

    const dbg = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(dbg.body.responseMode, 'lite');
    assert.equal(dbg.body.totemCount, 2);
    assert.equal(dbg.body.totemQuantity, 4);
    assert.equal(dbg.body.fishCount, 1);
    assert.equal(dbg.body.stoneCount, 1);
    assert.equal(dbg.body.debugUpload, true);
    assert.equal(dbg.body.hasInventoryClassificationDebug, true);
    assert.equal(dbg.body.totemItems, undefined);

    process.env.FISHIT_GLOBAL_ADMIN_TOKEN = ADMIN_TOKEN;
    const fullDbg = await request(app)
      .get(`/api/fishit-tracker/debug/${username}?full=1&admin_token=${ADMIN_TOKEN}`)
      .expect(200);
    assert.equal(fullDbg.body.responseMode, 'full');
    assert.ok(Array.isArray(fullDbg.body.totemItems));
    assert.equal(fullDbg.body.totemItems.length, 2);
    assert.equal(fullDbg.body.uploadPipelineDiagnostics.totemCount, 2);
    assert.equal(fullDbg.body.uploadPipelineDiagnostics.totemQuantity, 4);
    assert.ok(fullDbg.body.totemScanProof);
    assert.equal(fullDbg.body.totemScanProof.count, 2);
    assert.deepEqual(fullDbg.body.totemScanProof.names.sort(), ['Mutation Totem', 'Shiny Totem']);
    assert.ok(fullDbg.body.inventoryItemClassificationDebug);
    assert.equal(fullDbg.body.inventoryItemClassificationDebug.unresolvedRows, 40);
    assert.equal(fullDbg.body.uploadPipelineDiagnostics.rawUploadTotemCount, 2);

    const latest = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.ok(Array.isArray(latest.body.totemItems));
    assert.equal(latest.body.totemItems.length, 2);
    const names = latest.body.totemItems.map((t) => t.name).sort();
    assert.deepEqual(names, ['Mutation Totem', 'Shiny Totem']);
    const mutation = latest.body.totemItems.find((t) => t.name === 'Mutation Totem');
    assert.equal(mutation.amount || mutation.quantity, 3);
    assert.ok(Array.isArray(latest.body.stoneItems));
    assert.equal(latest.body.stoneItems.length, 1);
    assert.ok(Array.isArray(latest.body.fishItems));
    assert.equal(latest.body.fishItems.length, 1);
  });

  test('backend persists path audit fields and heartbeat preserves inventory snapshot diagnostics', async () => {
    const app = makeTrackerApp();
    const username = 'TotemAuditUser';
    const pathAudit = {
      searchedTerms: ['Totem', 'Mutation Totem'],
      matches: [{ path: 'Inventory.Gears', key: 'Name', value: 'Mutation Totem', matchedTerm: 'Totem' }],
      inventoryPathCounts: { 'Inventory.Items': 'table:55', 'Inventory.Gears': 'table:61' },
    };
    const gameItemDbAudit = {
      totemNameMatches: [{ itemId: '501', name: 'Mutation Totem', type: 'Gears' }],
      gearSamples: [{ itemId: '501', name: 'Mutation Totem', type: 'Gears' }],
      trophySamples: [],
      artifactMatches: [],
      mutationMatches: [{ itemId: '501', name: 'Mutation Totem', type: 'Gears' }],
    };
    const classificationDebug = {
      totalRows: 116,
      scannedRows: 116,
      skippedNoIdRows: 0,
      fishRows: 7,
      stoneRows: 2,
      totemRows: 0,
      totemTypeCount: 0,
      totemEffectiveQty: 0,
      unresolvedRows: 0,
      totemNotFoundReason: 'totem_term_found_in_replion_but_not_classified',
      nonFishNonStoneItemGroups: [{
        id: '501',
        type: 'Gears',
        name: 'Mutation Totem',
        rows: 61,
        effectiveQty: 61,
        classificationDecision: 'ignored_known_non_totem',
        reason: 'gear_or_trophy_not_totem',
      }],
      totemPathAudit: pathAudit,
      gameItemDbTotemAudit: gameItemDbAudit,
    };

    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(buildUploadBody(username, {
        debugUpload: true,
        fishItems: [{ kind: 'fish', itemId: '70', name: 'Clownfish', quantity: 1, source: 'playerdata_gameitemdb' }],
        stoneItems: [stoneRow('Normal', '10', 1)],
        totemPathAudit: pathAudit,
        gameItemDbTotemAudit: gameItemDbAudit,
        nonFishNonStoneItemGroups: classificationDebug.nonFishNonStoneItemGroups,
        inventoryItemClassificationDebug: classificationDebug,
      }))
      .expect(200);

    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username,
        userId: 9001,
        trackerBuild: MINIMUM_TRACKER_BUILD,
        trackerChannel: ALLOWED_TRACKER_CHANNEL,
        scriptSource: ALLOWED_TRACKER_RAW_URL,
        trackerClientProof: {
          trackerBuild: MINIMUM_TRACKER_BUILD,
          trackerChannel: ALLOWED_TRACKER_CHANNEL,
          scriptSource: ALLOWED_TRACKER_RAW_URL,
        },
        isOnline: true,
        online: true,
        phase: 'live',
      })
      .expect(200);

    process.env.FISHIT_GLOBAL_ADMIN_TOKEN = ADMIN_TOKEN;
    const dbg = await request(app)
      .get(`/api/fishit-tracker/debug/${username}?full=1&admin_token=${ADMIN_TOKEN}`)
      .expect(200);
    assert.equal(dbg.body.responseMode, 'full');
    assert.ok(dbg.body.totemPathAudit);
    assert.equal(dbg.body.totemPathAudit.inventoryPathCounts['Inventory.Gears'], 'table:61');
    assert.ok(dbg.body.gameItemDbTotemAudit);
    assert.equal(dbg.body.gameItemDbTotemAudit.totemNameMatches[0].name, 'Mutation Totem');
    assert.ok(dbg.body.inventoryItemClassificationDebug);
    assert.equal(dbg.body.inventoryItemClassificationDebug.totemNotFoundReason,
      'totem_term_found_in_replion_but_not_classified');
    assert.ok(Array.isArray(dbg.body.nonFishNonStoneItemGroups));
    assert.equal(dbg.body.nonFishNonStoneItemGroups[0].effectiveQty, 61);
    assert.ok(dbg.body.lastInventorySnapshotDiagnostics);
    assert.equal(dbg.body.lastInventorySnapshotPayloadType, 'inventory_snapshot');
    assert.ok(dbg.body.lastHeartbeatDiagnostics);
    assert.equal(dbg.body.lastHeartbeatDiagnostics.payloadType, 'tracker_status');
    assert.equal(dbg.body.uploadPipelineDiagnostics.hasInventoryClassificationDebug, true);
    assert.equal(dbg.body.uploadPipelineDiagnostics.fishCount, 1);
  });

  test('production compact debug upload does not synthesize legacy audit skeleton', async () => {
    process.env.FISHIT_GLOBAL_ADMIN_TOKEN = ADMIN_TOKEN;
    const app = makeTrackerApp();
    const username = 'TotemSkelUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(buildUploadBody(username, {
        debugUpload: true,
        fishItems: [{ kind: 'fish', itemId: '70', name: 'Clownfish', quantity: 1, source: 'playerdata_gameitemdb' }],
        stoneItems: [stoneRow('Normal', '10', 1)],
      }))
      .expect(200);

    const lite = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(lite.body.responseMode, 'lite');
    assert.equal(lite.body.hasInventoryClassificationDebug, false);

    const dbg = await request(app)
      .get(`/api/fishit-tracker/debug/${username}?full=1&admin_token=${ADMIN_TOKEN}`)
      .expect(200);
    assert.equal(dbg.body.responseMode, 'full');
    assert.equal(dbg.body.inventoryItemClassificationDebug, null);
    assert.equal(dbg.body.totemPathAudit, null);
    assert.equal(dbg.body.gameItemDbTotemAudit, null);
    assert.deepEqual(dbg.body.nonFishNonStoneItemGroups, []);
    assert.equal(dbg.body.uploadPipelineDiagnostics.hasInventoryClassificationDebug, false);
  });

  test('backend persists totemInventoryPathProof with notification cross-check qty 61', async () => {
    const app = makeTrackerApp();
    const username = 'TotemPathUser';
    const totemInventoryPathProof = {
      path: 'Inventory.Totems',
      rowCount: 61,
      effectiveQuantity: 61,
      notificationQuantity: 61,
      sampleRows: [{ uuid: 'uuid-1', itemId: '501', quantity: 1 }],
      classificationSource: 'inventory_totems_path_notification_count_verified',
    };
    const pathAudit = {
      searchedTerms: ['Totem'],
      matches: [{ path: 'Inventory.Totems', key: 'Totems', value: 'table', matchedTerm: 'Totem' }],
      inventoryPathCounts: {
        'Inventory.Totems': 'table:61',
        'InventoryNotifications.Totems': 61,
        'Inventory.Items': 'table:72',
      },
    };

    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(buildUploadBody(username, {
        debugUpload: true,
        fishItems: [{
          kind: 'fish', itemId: '70', name: 'Clownfish', quantity: 1,
          source: 'playerdata_gameitemdb', identityVerified: true,
        }],
        stoneItems: [stoneRow('Normal', '10', 1081)],
        totemItems: [totemRow('Mutation Totem', '501', 61, {
          resolveSource: 'inventory_totems_path_notification_count_verified',
        })],
        totemInventoryPathProof,
        totemPathAudit: pathAudit,
        nonFishNonStoneItemGroups: [{
          id: 267,
          type: 'Gears',
          name: 'Diamond Artifact',
          rows: 32,
          effectiveQty: 32,
          classificationDecision: 'ignored_known_non_totem',
          reason: 'gear_or_trophy_not_totem',
        }],
      }))
      .expect(200);

    const lite = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.equal(lite.body.responseMode, 'lite');
    assert.equal(lite.body.totemCount, 1);
    assert.equal(lite.body.totemQuantity, 61);
    assert.equal(lite.body.fishCount, 1);
    assert.equal(lite.body.stoneCount, 1);
    assert.equal(lite.body.totemInventoryPathProof, undefined);

    process.env.FISHIT_GLOBAL_ADMIN_TOKEN = ADMIN_TOKEN;
    const dbg = await request(app)
      .get(`/api/fishit-tracker/debug/${username}?full=1&admin_token=${ADMIN_TOKEN}`)
      .expect(200);
    assert.equal(dbg.body.responseMode, 'full');
    assert.ok(dbg.body.totemInventoryPathProof);
    assert.equal(dbg.body.totemInventoryPathProof.path, 'Inventory.Totems');
    assert.equal(dbg.body.totemInventoryPathProof.effectiveQuantity, 61);
    assert.equal(dbg.body.totemInventoryPathProof.notificationQuantity, 61);
    assert.equal(dbg.body.uploadPipelineDiagnostics.totemQuantity, 61);
    assert.equal(dbg.body.totemItems.length, 1);
    assert.equal(dbg.body.totemItems[0].name, 'Mutation Totem');
    assert.equal(dbg.body.totemPathAudit.inventoryPathCounts['Inventory.Totems'], 'table:61');
    assert.equal(dbg.body.nonFishNonStoneItemGroups[0].name, 'Diamond Artifact');
    assert.equal(dbg.body.uploadPipelineDiagnostics.fishCount, 1);
    assert.equal(dbg.body.uploadPipelineDiagnostics.stoneCount, 1);
  });

  test('get-backpack API returns totemItems for AIO/tracker consumers', async () => {
    const app = makeTrackerApp();
    const username = 'TotemBackpackUser';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(buildUploadBody(username, {
        totemItems: [totemRow('Ancient Totem', '503', 2)],
      }))
      .expect(200);

    const latest = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.ok(Array.isArray(latest.body.totemItems));
    assert.equal(latest.body.totemItems.length, 1);
    assert.equal(latest.body.totemItems[0].name, 'Ancient Totem');
  });

  test('backend upload_persist log uses fishCount stoneCount totemCount totemQuantity', () => {
    const routesSrc = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'fishitTrackerRoutes.js'),
      'utf8',
    );
    assert.match(routesSrc, /upload_persist[\s\S]*fishCount=%d stoneCount=%d totemCount=%d totemQuantity=%d/);
    assert.match(routesSrc, /persistedTotemPathAudit=%s persistedClassificationDebug=%s persistedNonFishGroups=%d/);
    assert.match(routesSrc, /upload_arrival[\s\S]*hasTotemPathAudit=%s hasTotemInventoryPathProof=%s hasClassificationDebug=%s hasGameItemDbTotemAudit=%s/);
    assert.match(routesSrc, /function buildTotemScanProof/);
    assert.match(routesSrc, /totemScanProof/);
    assert.match(routesSrc, /totemItems: lite\.totemItems/);
  });

  test('backward compatible when totemItems omitted', async () => {
    const app = makeTrackerApp();
    const username = 'TotemUserLegacy';
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(buildUploadBody(username, { stoneItems: [stoneRow('Normal', '10', 1)] }))
      .expect(200);

    const latest = await request(app).get(`/api/fishit-tracker/get-backpack/${username}`).expect(200);
    assert.ok(Array.isArray(latest.body.totemItems));
    assert.equal(latest.body.totemItems.length, 0);
    assert.equal(latest.body.stoneItems.length, 1);
  });
});
