'use strict';
/**
 * Tests for the Replion-driven Fish It tracker payloads (fishitTrackerRoutes.js).
 *
 * Build: v7-replion — Replion replicated player data is the inventory source
 * of truth. These tests verify the backend ingestion contract:
 *   - source="replion" is accepted and stored.
 *   - inventory_snapshot REPLACES the stored items (counts never accumulate).
 *   - tracker_status is status-only: it keeps the last inventory and only
 *     flips the online flag / source.
 *   - Going offline keeps the last known inventory visible.
 *   - Replion field aliases (count, maxWeight, tier) are normalised.
 */

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');

process.env.NODE_ENV = 'test';
const TMP_CATALOG = path.join(os.tmpdir(), `fishit_replion_test_${process.pid}.json`);
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

describe('Fish It tracker — Replion payloads (v7-replion)', () => {
  beforeEach(() => { cleanup(); });

  test('accepts source="replion" inventory_snapshot and stores the source', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 3, tier: 'secret' }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/ReplionAngler')
      .expect(200);

    assert.equal(res.body.source, 'replion');
    const crab = res.body.items.find((i) => i.name === 'King Crab');
    assert.ok(crab, 'King Crab must be stored');
    assert.equal(crab.amount, 3, 'count alias must map to amount');
  });

  test('inventory_snapshot REPLACES items — counts never accumulate', async () => {
    const app = makeApp();

    // First snapshot: 3 King Crab.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 3 }],
      })
      .expect(200);

    // Second snapshot: still 3 King Crab (same authoritative state).
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 3 }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/ReplionAngler')
      .expect(200);

    const crabs = res.body.items.filter((i) => i.name === 'King Crab');
    assert.equal(crabs.length, 1, 'snapshot must not duplicate entries');
    assert.equal(crabs[0].amount, 3, 'count must stay 3 — replaced, not summed');
  });

  test('inventory_snapshot with fewer items replaces (removes sold fish)', async () => {
    const app = makeApp();

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'King Crab', count: 3 },
          { name: 'Clownfish', count: 10 },
        ],
      })
      .expect(200);

    // Player sold the Clownfish — next snapshot only has King Crab.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 3 }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/ReplionAngler')
      .expect(200);

    const names = res.body.items.map((i) => i.name);
    assert.ok(names.includes('King Crab'), 'King Crab kept');
    assert.ok(!names.includes('Clownfish'), 'sold Clownfish must be gone');
  });

  test('tracker_status is status-only: keeps inventory, flips online flag', async () => {
    const app = makeApp();

    // Seed an inventory while online.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 3 }],
      })
      .expect(200);

    // Status-only ping marking the player offline (no items in the body).
    const ping = await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: false,
      })
      .expect(200);

    assert.equal(ping.body.note, 'status_only');

    const res = await request(app)
      .get('/api/tracker/get-backpack/ReplionAngler')
      .expect(200);

    assert.equal(res.body.isOnline, false, 'must be marked offline');
    const crab = res.body.items.find((i) => i.name === 'King Crab');
    assert.ok(crab, 'inventory must persist through a status-only ping');
    assert.equal(crab.amount, 3);
  });

  test('offline snapshot with empty items keeps the last known inventory', async () => {
    const app = makeApp();

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 3 }],
      })
      .expect(200);

    // Offline + empty: must NOT wipe the inventory.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: false,
        items: [],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/ReplionAngler')
      .expect(200);

    assert.equal(res.body.isOnline, false);
    assert.ok(res.body.items.some((i) => i.name === 'King Crab'), 'inventory kept while offline');
  });

  test('coming back online with a fresh snapshot flips green + updates items', async () => {
    const app = makeApp();

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: false,
        items: [{ name: 'King Crab', count: 3 }],
      })
      .expect(200);

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Clownfish', count: 7 }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/ReplionAngler')
      .expect(200);

    assert.equal(res.body.isOnline, true, 'must be back online');
    const names = res.body.items.map((i) => i.name);
    assert.ok(names.includes('Clownfish'), 'fresh snapshot applied');
    assert.ok(!names.includes('King Crab'), 'old snapshot replaced');
  });

  test('unknown / disallowed source is normalised to "unknown"', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplionAngler',
        userId: 555,
        source: 'totally-made-up',
        isOnline: true,
        items: [{ name: 'King Crab', count: 1 }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/ReplionAngler')
      .expect(200);

    assert.equal(res.body.source, 'unknown');
  });
});
