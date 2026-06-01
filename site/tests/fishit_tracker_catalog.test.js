'use strict';
/**
 * Tests for the Fish It live tracker catalog pipeline (fishitTrackerRoutes.js +
 * fishitCatalogStore.js).
 *
 * Verifies:
 *   - Stat labels ("Caught", "Rarest Fish") are rejected on write.
 *   - A real fish is accepted and, after a catalog snapshot, GET returns the
 *     catalog's tier + image merged into the inventory card.
 *   - Old snapshots that still contain stat labels are filtered on read.
 *   - Going offline keeps the last known inventory (status-only update).
 *   - The catalog snapshot endpoint stores fish metadata persistently.
 */

const { describe, test, before, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');

process.env.NODE_ENV = 'test';
// Isolate the catalog store to a temp file so we never touch site/data.
const TMP_CATALOG = path.join(os.tmpdir(), `fishit_catalog_test_${process.pid}.json`);
process.env.FISHIT_CATALOG_PATH = TMP_CATALOG;

const express = require('express');
const request = require('supertest');
const catalogStore = require('../src/fishitCatalogStore');
const trackerRouter = require('../src/fishitTrackerRoutes');

function makeApp() {
  const app = express();
  app.use(trackerRouter);
  return app;
}

function cleanup() {
  try { if (fs.existsSync(TMP_CATALOG)) fs.unlinkSync(TMP_CATALOG); } catch (_) {}
  catalogStore._reset();
}

describe('Fish It tracker catalog pipeline', () => {
  beforeEach(() => { cleanup(); });

  test('rejects "Caught" and "Rarest Fish" stat labels on write', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'TestAngler',
        userId: 123,
        isOnline: true,
        items: [
          { name: 'Caught', amount: 1567 },
          { name: 'Rarest Fish', amount: 1 },
          { name: 'King Crab', amount: 3, rarity: 'secret' },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/TestAngler')
      .expect(200);

    const names = res.body.items.map((i) => i.name);
    assert.ok(!names.includes('Caught'), 'Caught must be filtered');
    assert.ok(!names.includes('Rarest Fish'), 'Rarest Fish must be filtered');
    assert.ok(names.includes('King Crab'), 'real fish must be kept');
  });

  test('catalog snapshot merges tier + image into inventory on read', async () => {
    const app = makeApp();

    // 1) Send a recursive catalog snapshot (as the Lua tracker would).
    await request(app)
      .post('/api/tracker/update-catalog')
      .send({
        type: 'fish_catalog_snapshot',
        playerName: 'TestAngler',
        userId: 123,
        catalog: {
          fish: [
            {
              key: 'yellow damselfish',
              name: 'Yellow Damselfish',
              tier: 'legendary',
              imageUrl: 'https://www.roblox.com/asset-thumbnail/image?assetId=42&width=150&height=150&format=png',
              source: 'ReplicatedStorage.Items.Fish',
            },
          ],
          rods: [],
          items: [],
        },
      })
      .expect(200);

    // 2) Inventory arrives WITHOUT tier/image (the regression scenario).
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'TestAngler',
        userId: 123,
        isOnline: true,
        items: [{ name: 'Yellow Damselfish', amount: 2 }],
      })
      .expect(200);

    // 3) GET merges catalog metadata into the card.
    const res = await request(app)
      .get('/api/tracker/get-backpack/TestAngler')
      .expect(200);

    const fish = res.body.items.find((i) => i.name === 'Yellow Damselfish');
    assert.ok(fish, 'fish present');
    assert.equal(fish.rarity, 'legend', 'tier normalized legendary -> legend');
    assert.match(fish.imageUrl, /asset-thumbnail/, 'catalog image merged');
  });

  test('old snapshot stat labels are filtered on read even if stored', async () => {
    const app = makeApp();

    // Send a valid fish so the user exists.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'TestAngler',
        userId: 123,
        isOnline: true,
        items: [{ name: 'King Crab', amount: 1 }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/TestAngler')
      .expect(200);

    for (const it of res.body.items) {
      assert.ok(!catalogStore.isStatLabel(it.name), `${it.name} must not be a stat label`);
    }
  });

  test('offline status-only update keeps last known inventory', async () => {
    const app = makeApp();

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'TestAngler',
        userId: 123,
        isOnline: true,
        items: [{ name: 'King Crab', amount: 5 }],
      })
      .expect(200);

    // Offline ping with empty items must NOT clear inventory.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({ username: 'TestAngler', userId: 123, isOnline: false, items: [] })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/TestAngler')
      .expect(200);

    assert.equal(res.body.isOnline, false, 'flag is offline');
    const fish = res.body.items.find((i) => i.name === 'King Crab');
    assert.ok(fish, 'inventory still present while offline');
    assert.equal(fish.amount, 5, 'amount preserved');
  });

  test('catalog snapshot is persisted and retrievable', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-catalog')
      .send({
        type: 'fish_catalog_snapshot',
        playerName: 'TestAngler',
        catalog: {
          fish: [{ key: 'king crab', name: 'King Crab', tier: 'secret', imageUrl: 'https://cdn/king.png' }],
          rods: [],
          items: [],
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/catalog')
      .expect(200);

    assert.ok(res.body.entries['king crab'], 'entry stored under normalized key');
    assert.equal(res.body.entries['king crab'].tier, 'secret');
    assert.ok(fs.existsSync(TMP_CATALOG), 'catalog persisted to disk');
  });

  test('rejects invalid catalog snapshot type', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-catalog')
      .send({ type: 'not_a_catalog', catalog: {} })
      .expect(400);
  });
});
