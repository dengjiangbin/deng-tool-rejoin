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
const { STONE_PUBLIC_MAP } = require('../src/fishitStoneDisplayMap');
const trackerRoutes = require('../src/fishitTrackerRoutes');
const sessionStore = require('../src/fishitSessionStore');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');

const QUANTITIES = [501, 999, 1000, 1500, 2500];

const STONE_FIXTURES = [
  { label: 'Normal Enchant Stone', stoneType: 'Normal', itemId: '10' },
  { label: 'Transcended Stone', stoneType: 'Double', itemId: '246' },
  { label: 'Evolved Enchant Stone', stoneType: 'Evolved', itemId: '558' },
  { label: 'Eggy Enchant Stone', stoneType: 'Eggy', itemId: '873' },
  { label: 'Runic Enchant Stone', stoneType: 'Runic', itemId: '929' },
  { label: 'Future Unknown Stone', stoneType: 'FutureStone', itemId: '99001' },
];

function stoneRow(stoneType, itemId, quantity, uuid) {
  const meta = STONE_PUBLIC_MAP[itemId];
  return {
    kind: 'stone',
    itemId: String(itemId),
    name: meta?.displayName || `${stoneType} Enchant Stone`,
    stoneType,
    quantity,
    uuid: uuid || `uuid-${stoneType}-${quantity}-${Math.random()}`,
    source: 'playerdata_gameitemdb',
    identityVerified: true,
    inventoryPath: 'Inventory.Enchant Stones',
  };
}

function manySingleStackRows(stoneType, itemId, count) {
  const rows = [];
  for (let i = 0; i < count; i += 1) {
    rows.push(stoneRow(stoneType, itemId, 1, `stack-${stoneType}-${i}`));
  }
  return rows;
}

function qtyByType(items, stoneType) {
  return (items || [])
    .filter((row) => String(row.stoneType || row.StoneType) === stoneType)
    .reduce((sum, row) => sum + (Number(row.quantity ?? row.amount ?? row.count) || 0), 0);
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

describe('all_enchant_stones_quantity_gt_500', () => {
  for (const fixture of STONE_FIXTURES) {
    describe(fixture.label, () => {
      for (const qty of QUANTITIES) {
        test(`groupStoneRows aggregates ${qty} single-qty rows`, () => {
          const grouped = gameItemDbPublic.groupStoneRows(manySingleStackRows(fixture.stoneType, fixture.itemId, qty));
          assert.equal(grouped.length, 1, `${fixture.stoneType} must collapse to one grouped row`);
          assert.equal(grouped[0].quantity, qty);
          assert.equal(gameItemDbPublic.mapToPublicStoneCardItem(grouped[0]).quantity, qty);
        });
      }

      test('session store aggregates before trim, not after raw slice', () => {
        const trimmed = sessionStore.sanitiseSession('allstoneuser', {
          username: 'allstoneuser',
          playerDataStoneItems: manySingleStackRows(fixture.stoneType, fixture.itemId, 750),
        });
        assert.equal(trimmed.playerDataStoneItems.length, 1);
        assert.equal(trimmed.playerDataStoneItems[0].quantity, 750);
      });

      test('ingest + API read preserves duplicate stacks above 500', async () => {
        const tmpStore = path.join(os.tmpdir(), `fishit-all-stone-${fixture.stoneType}-${Date.now()}.json`);
        process.env.FISHIT_LIVE_SESSIONS_PATH = tmpStore;
        sessionStore._reset();

        const key = `allstone${fixture.stoneType.toLowerCase().replace(/[^a-z0-9]/g, '')}`;
        const app = makeApp();
        const body = {
          type: 'inventory_snapshot',
          username: key,
          userId: 9100 + fixture.itemId.length,
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
            stoneRow(fixture.stoneType, fixture.itemId, 501, 'stack-a'),
            stoneRow(fixture.stoneType, fixture.itemId, 999, 'stack-b'),
          ],
          totemItems: [],
        };

        const uploadRes = await request(app).post('/api/fishit-tracker/update-backpack').send(body);
        assert.ok([200, 202].includes(uploadRes.status), `upload status ${uploadRes.status}`);

        await sessionStore.flushToDiskAsync({ priority: true });
        const stored = JSON.parse(fs.readFileSync(tmpStore, 'utf8')).sessions[key.toLowerCase()];
        assert.ok(stored, 'session persisted');
        assert.equal(stored.playerDataStoneItems.length, 1, 'compact grouped row persisted');
        assert.equal(stored.playerDataStoneItems[0].quantity, 1500);

        const backpack = await request(app).get(`/api/tracker/get-backpack/${key}`).expect(200);
        assert.equal(qtyByType(backpack.body.stoneItems || backpack.body.stoneInventory, fixture.stoneType), 1500);

        sessionStore._reset();
        try { fs.unlinkSync(tmpStore); } catch (_) { /* ignore */ }
        delete process.env.FISHIT_LIVE_SESSIONS_PATH;
      });
    });
  }

  test('different stone types do not merge during grouping', () => {
    const rows = STONE_FIXTURES.flatMap((fixture) => [
      stoneRow(fixture.stoneType, fixture.itemId, 100, `a-${fixture.stoneType}`),
      stoneRow(fixture.stoneType, fixture.itemId, 50, `b-${fixture.stoneType}`),
    ]);
    const grouped = gameItemDbPublic.groupStoneRows(rows);
    assert.equal(grouped.length, STONE_FIXTURES.length);
    for (const fixture of STONE_FIXTURES) {
      assert.equal(qtyByType(grouped, fixture.stoneType), 150, `${fixture.stoneType} must stay isolated`);
    }
  });

  test('preferHigherGroupedStoneSnapshot is generic, not Evolved-only', () => {
    for (const fixture of STONE_FIXTURES) {
      const live = gameItemDbPublic.groupStoneRows([stoneRow(fixture.stoneType, fixture.itemId, 500)]);
      const preserved = [stoneRow(fixture.stoneType, fixture.itemId, 1534)];
      const resolved = gameItemDbPublic.preferHigherGroupedStoneSnapshot(live, preserved);
      assert.equal(resolved[0].quantity, 1534, `${fixture.stoneType} preserved snapshot must win`);
    }
  });

  test('implementation has no Evolved-only branch in stone trim/group helpers', () => {
    const sessionSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitSessionStore.js'), 'utf8');
    const routesSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitTrackerRoutes.js'), 'utf8');
    const publicSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitGameItemDbPublic.js'), 'utf8');
    assert.doesNotMatch(sessionSrc, /Evolved/);
    assert.doesNotMatch(publicSrc, /Evolved Enchant Stone/);
    assert.match(sessionSrc, /groupStoneRows/);
    assert.match(routesSrc, /groupStoneRows\(playerDataStoneItemsRaw\)/);
    assert.match(publicSrc, /stoneIdentityKey/);
  });
});
