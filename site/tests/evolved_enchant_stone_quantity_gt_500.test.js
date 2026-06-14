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

function evolvedStoneRow(quantity, uuid) {
  return {
    kind: 'stone',
    itemId: EVOLVED_ID,
    name: 'Evolved Enchant Stone',
    stoneType: 'Evolved',
    quantity,
    uuid: uuid || `uuid-${quantity}-${Math.random()}`,
    source: 'playerdata_gameitemdb',
    identityVerified: true,
    inventoryPath: 'Inventory.Enchant Stones',
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

function evolvedQtyFromApiBody(body) {
  const stones = body.stoneItems || body.stoneInventory || [];
  return stones
    .filter((s) => String(s.stoneType || s.StoneType) === 'Evolved')
    .reduce((sum, row) => sum + (Number(row.quantity ?? row.amount ?? row.count) || 0), 0);
}

function manySingleStackRows(count) {
  const rows = [];
  for (let i = 0; i < count; i += 1) {
    rows.push(evolvedStoneRow(1, `stack-${i}`));
  }
  return rows;
}

describe('evolved_enchant_stone_quantity_gt_500', () => {
  for (const qty of [501, 999, 1000, 1500, 2500]) {
    test(`groupStoneRows preserves single aggregated row quantity ${qty}`, () => {
      const grouped = gameItemDbPublic.groupStoneRows([evolvedStoneRow(qty)]);
      assert.equal(grouped.length, 1);
      assert.equal(grouped[0].quantity, qty);
      assert.equal(gameItemDbPublic.mapToPublicStoneCardItem(grouped[0]).quantity, qty);
    });
  }

  test('real stokjualanardian pattern: 1534 single-qty stacks must not collapse to 500 after persist', async () => {
    const tmpStore = path.join(os.tmpdir(), `fishit-stone-trunc-${Date.now()}.json`);
    process.env.FISHIT_LIVE_SESSIONS_PATH = tmpStore;
    sessionStore._reset();

    const key = 'stokjualanardian';
    const username = 'StokJualanArdian';
    const app = makeApp();
    const uploadRows = manySingleStackRows(1534);
    const body = {
      type: 'inventory_snapshot',
      username,
      userId: 9002,
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
      stoneItems: uploadRows,
      totemItems: [],
    };

    const uploadRes = await request(app).post('/api/fishit-tracker/update-backpack').send(body);
    assert.ok([200, 202].includes(uploadRes.status), `upload status ${uploadRes.status}`);

    await sessionStore.flushToDiskAsync({ priority: true });
    const stored = JSON.parse(fs.readFileSync(tmpStore, 'utf8')).sessions[key.toLowerCase()];
    assert.ok(stored, 'session persisted');
    assert.equal(stored.playerDataStoneItems.length, 1, 'stones aggregated before persist');
    assert.equal(stored.playerDataStoneItems[0].quantity, 1534);
    assert.equal(stored.lastGoodPublicStoneItems[0].quantity, 1534);

    const backpack = await request(app).get(`/api/tracker/get-backpack/${key}`).expect(200);
    assert.equal(evolvedQtyFromApiBody(backpack.body), 1534);

    sessionStore._reset();
    try { fs.unlinkSync(tmpStore); } catch (_) { /* ignore */ }
    delete process.env.FISHIT_LIVE_SESSIONS_PATH;
  });

  test('ingest + API read preserves duplicate-row stacks that sum above 500', async () => {
    const tmpStore = path.join(os.tmpdir(), `fishit-stone-dup-${Date.now()}.json`);
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
        evolvedStoneRow(501, 'stack-a'),
        evolvedStoneRow(999, 'stack-b'),
      ],
      totemItems: [],
    };

    const uploadRes = await request(app).post('/api/fishit-tracker/update-backpack').send(body);
    assert.ok([200, 202].includes(uploadRes.status), `upload status ${uploadRes.status}`);

    await sessionStore.flushToDiskAsync({ priority: true });
    const backpack = await request(app).get(`/api/tracker/get-backpack/${key}`).expect(200);
    assert.equal(evolvedQtyFromApiBody(backpack.body), 1500);

    sessionStore._reset();
    try { fs.unlinkSync(tmpStore); } catch (_) { /* ignore */ }
    delete process.env.FISHIT_LIVE_SESSIONS_PATH;
  });

  test('preferHigherGroupedStoneSnapshot keeps last-good when live rebuild undercounts', () => {
    const live = [evolvedStoneRow(500)];
    const preserved = [evolvedStoneRow(1534)];
    const resolved = gameItemDbPublic.preferHigherGroupedStoneSnapshot(
      gameItemDbPublic.groupStoneRows(live),
      preserved,
    );
    assert.equal(resolved[0].quantity, 1534);
  });

  test('session store aggregates truncated legacy raw rows on reload', () => {
    const trimmed = sessionStore.sanitiseSession('legacyuser', {
      username: 'legacyuser',
      playerDataStoneItems: manySingleStackRows(750),
    });
    assert.equal(trimmed.playerDataStoneItems.length, 1);
    assert.equal(trimmed.playerDataStoneItems[0].quantity, 750);
  });

  testIfRawTracker('Lua aggregates enchant stone stacks before upload', () => {
    const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
    assert.match(lua, /aggregateStoneItemsByType/);
    assert.match(lua, /Inventory\.Enchant Stones/);
    assert.match(lua, /logStoneAggregateProof/);
    assert.doesNotMatch(lua, /1500/);
  });
});
