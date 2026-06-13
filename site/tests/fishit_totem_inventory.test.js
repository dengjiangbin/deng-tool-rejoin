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

  test('groupTotemRows sums quantity by uuid/itemId', () => {
    const grouped = gameItemDbPublic.groupTotemRows([
      totemRow('Mutation Totem', '501', 2, { uuid: 'a' }),
      totemRow('Mutation Totem', '501', 1, { uuid: 'a' }),
      totemRow('Shiny Totem', '502', 4, { uuid: 'b' }),
    ]);
    assert.equal(grouped.length, 2);
    const mutation = grouped.find((r) => r.name === 'Mutation Totem');
    assert.equal(mutation.quantity, 3);
  });

  test('loader scan source detects totem name match', () => {
    const lua = fs.readFileSync(path.join(__dirname, '..', '..', '_test_converted.lua'), 'utf8');
    assert.match(lua, /totemItems/);
    assert.match(lua, /string\.find\(string\.lower\(tostring\(data\.Name\)\), "totem"/);
    assert.match(lua, /Mutation Totem|Shiny Totem|totemItems = gameItemScan\.totemItems/);
  });

  test('frontend source exposes Item Grid and Totem titles', () => {
    const source = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'),
      'utf8',
    );
    assert.match(source, /Item Grid/);
    assert.match(source, /Enchant Stones \(\$\{formatQuantity\(stoneTotal\)\}\)/);
    assert.match(source, /Totem \(\$\{formatQuantity\(totemTotal\)\}\)/);
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
    });

    const upload = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(body)
      .expect(200);
    assert.equal(upload.body.ok, true);

    const dbg = await request(app).get(`/api/fishit-tracker/debug/${username}`).expect(200);
    assert.ok(Array.isArray(dbg.body.totemItems));
    assert.equal(dbg.body.totemItems.length, 2);
    assert.equal(dbg.body.uploadPipelineDiagnostics.totemCount, 2);
    assert.equal(dbg.body.uploadPipelineDiagnostics.totemQuantity, 4);

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
