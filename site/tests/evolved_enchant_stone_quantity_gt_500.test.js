'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';
process.env.FISHIT_SESSION_SYNC_SAVE = '1';

const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const trackerRoutes = require('../src/fishitTrackerRoutes');
const sessionStore = require('../src/fishitSessionStore');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const { RAW_TRACKER_LUA, testIfRawTracker } = require('./helpers/trackerRawSource');

const EVOLVED_ID = 558;

function evolvedStoneRow(quantity, uuid, pathLabel) {
  return {
    kind: 'stone',
    itemId: EVOLVED_ID,
    name: 'Evolved Enchant Stone',
    stoneType: 'Evolved',
    quantity,
    uuid: uuid || `uuid-${quantity}-${Math.random()}`,
    source: 'playerdata_gameitemdb',
    identityVerified: true,
    inventoryPath: pathLabel || 'Inventory.Enchant Stones',
  };
}

function makeApp() {
  const app = express();
  app.use(express.json({ limit: '512kb' }));
  app.use((req, _res, next) => {
    req.inventoryOwnerDiscordId = '123456789012345678';
    next();
  });
  app.use(trackerRoutes);
  return app;
}

describe('evolved_enchant_stone_quantity_gt_500', () => {
  test('groupStoneRows sums three 500 stacks to 1500', () => {
    const rows = [
      evolvedStoneRow(500, 'a'),
      evolvedStoneRow(500, 'b'),
      evolvedStoneRow(500, 'c'),
    ];
    const grouped = gameItemDbPublic.groupStoneRows(rows);
    assert.equal(grouped.length, 1);
    assert.equal(grouped[0].stoneType, 'Evolved');
    assert.equal(grouped[0].quantity, 1500);
    const card = gameItemDbPublic.mapToPublicStoneCardItem(grouped[0]);
    assert.equal(card.quantity, 1500);
    assert.equal(card.amount, 1500);
  });

  test('groupStoneRows preserves single stack 1500 and 2500', () => {
    for (const qty of [1500, 2500]) {
      const grouped = gameItemDbPublic.groupStoneRows([evolvedStoneRow(qty)]);
      assert.equal(grouped[0].quantity, qty);
      assert.equal(gameItemDbPublic.mapToPublicStoneCardItem(grouped[0]).quantity, qty);
    }
  });

  test('ingest + API read returns evolved stone quantity above 500', async () => {
    const tmpStore = path.join(os.tmpdir(), `fishit-stone-qty-${Date.now()}.json`);
    process.env.FISHIT_LIVE_SESSIONS_PATH = tmpStore;
    sessionStore._reset();

    const key = 'stoneqtyuser';
    const username = 'StoneQtyUser';
    const app = makeApp();
    const body = {
      type: 'inventory_snapshot',
      username,
      userId: 9001,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      clientOrigin: 'roblox_tracker',
      evidenceSourceMode: 'live_roblox',
      intervalSeconds: 60,
      isOnline: true,
      inventorySource: 'playerdata_gameitemdb',
      playerStats: {
        coins: 1,
        totalCaught: 1,
        rarestFishChance: '1/100',
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
      fishItems: [],
      stoneItems: [
        evolvedStoneRow(500, 'stack-1', 'Inventory.Enchant Stones'),
        evolvedStoneRow(500, 'stack-2', 'Inventory.Enchant Stones'),
        evolvedStoneRow(500, 'stack-3', 'Inventory.Items'),
      ],
      totemItems: [],
    };

    const uploadRes = await request(app).post('/api/fishit-tracker/update-backpack').send(body);
    assert.ok([200, 202].includes(uploadRes.status), `upload status ${uploadRes.status}`);

    await sessionStore.flushToDiskAsync({ priority: true });

    const backpack = await request(app).get(`/api/tracker/get-backpack/${key}`).expect(200);
    const stones = backpack.body.stoneItems || backpack.body.stoneInventory || [];
    const evolved = stones.filter((s) => String(s.stoneType || s.StoneType) === 'Evolved');
    assert.ok(evolved.length >= 1, 'expected evolved stone in API response');
    const qty = evolved.reduce(
      (sum, row) => sum + (Number(row.quantity ?? row.amount ?? row.count) || 0),
      0,
    );
    assert.equal(qty, 1500, `API evolved stone qty=${qty}`);

    sessionStore._reset();
    try { fs.unlinkSync(tmpStore); } catch (_) { /* ignore */ }
    delete process.env.FISHIT_LIVE_SESSIONS_PATH;
  });

  testIfRawTracker('Lua scans Inventory.Enchant Stones and aggregates stone stacks', () => {
    const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
    assert.match(lua, /UPLOAD_502_HARDENING_AND_EVOLVED_STONE_QTY_FIX_2026_06_14/);
    assert.match(lua, /Inventory\.Enchant Stones/);
    assert.match(lua, /aggregateStoneItemsByType/);
    assert.match(lua, /logStoneAggregateProof/);
    assert.match(lua, /TRANSIENT_UPLOAD_BACKOFF/);
    assert.match(lua, /UPLOAD_RETRY_COUNT=/);
  });
});
