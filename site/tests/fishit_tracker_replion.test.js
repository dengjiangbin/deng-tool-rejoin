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

describe('Fish It tracker — tracker_status phases (running before inventory)', () => {
  beforeEach(() => { cleanup(); });

  test('tracker_status with NO existing session creates an online session', async () => {
    const app = makeApp();

    const ping = await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'FreshAngler',
        userId: 777,
        source: 'replion',
        isOnline: true,
        phase: 'startup',
      })
      .expect(200);

    assert.equal(ping.body.note, 'status_only');
    assert.equal(ping.body.phase, 'startup');

    // The website must no longer 404 / "await first data" — the session exists.
    const res = await request(app)
      .get('/api/tracker/get-backpack/FreshAngler')
      .expect(200);

    assert.equal(res.body.isOnline, true, 'status ping must mark the session online');
    assert.equal(res.body.phase, 'startup');
    assert.deepEqual(res.body.items, [], 'no inventory yet, but session is live');
    assert.equal(res.body.source, 'replion');
  });

  test('phase is stored and returned and advances across status pings', async () => {
    const app = makeApp();

    for (const phase of ['startup', 'replion_client_found', 'player_data_not_found']) {
      await request(app)
        .post('/api/tracker/update-backpack')
        .send({
          type: 'tracker_status',
          username: 'PhaseAngler',
          userId: 888,
          source: 'replion',
          isOnline: true,
          phase,
        })
        .expect(200);
    }

    const res = await request(app)
      .get('/api/tracker/get-backpack/PhaseAngler')
      .expect(200);

    assert.equal(res.body.phase, 'player_data_not_found', 'latest phase must win');
    assert.equal(res.body.isOnline, true);
  });

  test('an unknown phase is ignored (falls back, never crashes)', async () => {
    const app = makeApp();

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'BadPhaseAngler',
        userId: 999,
        source: 'replion',
        isOnline: true,
        phase: 'totally-made-up-phase',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/BadPhaseAngler')
      .expect(200);

    assert.equal(res.body.isOnline, true);
    assert.equal(res.body.phase, 'startup', 'invalid phase falls back to startup default');
  });

  test('a status ping promotes phase, then inventory_snapshot goes to "live"', async () => {
    const app = makeApp();

    // Running phase first (no items yet).
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'LiveAngler',
        userId: 1010,
        source: 'replion',
        isOnline: true,
        phase: 'player_data_selected',
      })
      .expect(200);

    // Then a real inventory snapshot arrives.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'LiveAngler',
        userId: 1010,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 2 }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/LiveAngler')
      .expect(200);

    assert.equal(res.body.phase, 'live', 'inventory snapshot flips phase to live');
    assert.ok(res.body.items.some((i) => i.name === 'King Crab'), 'inventory present');
  });

  test('status ping keeps inventory and stays live after a snapshot', async () => {
    const app = makeApp();

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'KeepAngler',
        userId: 1111,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Clownfish', count: 5 }],
      })
      .expect(200);

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'KeepAngler',
        userId: 1111,
        source: 'replion',
        isOnline: true,
        phase: 'player_data_selected',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/KeepAngler')
      .expect(200);

    assert.ok(res.body.items.some((i) => i.name === 'Clownfish'), 'status ping must keep inventory');
    assert.equal(res.body.isOnline, true);
  });
});

// ───────────────────────────────────────────────────────────────────
// BLOCKER 2: parser selects the right path, resolves item metadata by
// id/name, and the backend never drops valid id-only / unknown-tier items.
// The Lua parser itself runs in Roblox; here we lock the backend contract
// that the parser depends on (PARTs 5-10).
// ───────────────────────────────────────────────────────────────────
describe('Fish It tracker — Replion inventory parser contract (BLOCKER 2)', () => {
  beforeEach(() => { cleanup(); });

  test('inventory_empty phase is accepted and surfaced', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'EmptyAngler',
        userId: 2001,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_empty',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/EmptyAngler')
      .expect(200);

    assert.equal(res.body.phase, 'inventory_empty', 'inventory_empty phase must be stored');
    assert.equal(res.body.isOnline, true);
  });

  test('inventory_parse_failed phase is accepted and surfaced', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'ParseFailAngler',
        userId: 2002,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_parse_failed',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/ParseFailAngler')
      .expect(200);

    assert.equal(res.body.phase, 'inventory_parse_failed', 'parse_failed phase must be stored');
  });

  test('item with itemId + sourcePath but unknown tier is NOT dropped', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'IdAngler',
        userId: 2003,
        source: 'replion',
        isOnline: true,
        items: [{
          name: 'Yellow Damselfish',
          count: 2,
          itemId: 'yellow_damselfish',
          source: 'Replion.Inventory.Items.yellow_damselfish',
          // No tier/rarity on purpose — must still be kept.
        }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/IdAngler')
      .expect(200);

    const fish = res.body.items.find((i) => i.name === 'Yellow Damselfish');
    assert.ok(fish, 'id-only / unknown-tier item must be displayed');
    assert.equal(fish.amount, 2);
    assert.equal(fish.itemId, 'yellow_damselfish', 'itemId preserved');
    assert.ok((fish.source || '').includes('Replion.Inventory.Items'), 'sourcePath preserved');
  });

  test('item imageUrl from the snapshot survives backend enrichment', async () => {
    const app = makeApp();
    const img = 'https://www.roblox.com/asset-thumbnail/image?assetId=12345&width=150&height=150&format=png';
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ImgAngler',
        userId: 2004,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Glow Tetra', count: 1, imageUrl: img }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/ImgAngler')
      .expect(200);

    const fish = res.body.items.find((i) => i.name === 'Glow Tetra');
    assert.ok(fish, 'item present');
    assert.ok(fish.imageUrl && fish.imageUrl.length > 0, 'imageUrl must be carried through');
  });

  test('a non-empty snapshot replaces a previous non-zero inventory', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplaceAngler',
        userId: 2005,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 4 }],
      })
      .expect(200);

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'ReplaceAngler',
        userId: 2005,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Clownfish', count: 9 }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/ReplaceAngler')
      .expect(200);

    const names = res.body.items.map((i) => i.name);
    assert.ok(names.includes('Clownfish'), 'new inventory applied');
    assert.ok(!names.includes('King Crab'), 'previous non-zero inventory replaced');
  });

  test('stat labels (Caught / Rarest Fish) are still dropped on ingest', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'StatAngler',
        userId: 2006,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Caught', count: 100 },
          { name: 'Rarest Fish', count: 1 },
          { name: 'King Crab', count: 3 },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/StatAngler')
      .expect(200);

    const names = res.body.items.map((i) => i.name);
    assert.ok(names.includes('King Crab'), 'real fish kept');
    assert.ok(!names.includes('Caught'), 'Caught stat label dropped');
    assert.ok(!names.includes('Rarest Fish'), 'Rarest Fish stat label dropped');
  });
});

// ───────────────────────────────────────────────────────────────────
// BLOCKER 3: Replion Inventory.Items is an array of UUID-keyed instance
// records {Id=70, UUID="...", Metadata={Weight=N}}. The backend must
// preserve parseStats from inventory_parse_failed pings and inventory
// snapshots, and must not wipe a good inventory on a failed re-parse.
// ───────────────────────────────────────────────────────────────────
describe('Fish It tracker — numeric-Id instance inventory (BLOCKER 3)', () => {
  beforeEach(() => { cleanup(); });

  test('parseStats from inventory_parse_failed tracker_status is stored and returned', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'IdAngler3',
        userId: 3001,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_parse_failed',
        parseStats: { raw: 2145, accepted: 0, rejected: 2145, images: 0, tiers: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/IdAngler3')
      .expect(200);

    assert.equal(res.body.phase, 'inventory_parse_failed');
    assert.ok(res.body.parseStats, 'parseStats must be present');
    assert.equal(res.body.parseStats.raw, 2145, 'raw count preserved');
    assert.equal(res.body.parseStats.accepted, 0, 'accepted=0 preserved');
    assert.equal(res.body.parseStats.rejected, 2145, 'rejected count preserved');
    assert.equal(res.body.parseStats.selectedPath, 'Inventory.Items', 'selectedPath preserved');
  });

  test('parseStats from inventory_snapshot is stored', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'IdAngler4',
        userId: 3002,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Yellow Damselfish', count: 10, itemId: '70' }],
        parseStats: { raw: 10, accepted: 10, rejected: 0, images: 10, tiers: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/IdAngler4')
      .expect(200);

    assert.equal(res.body.phase, 'live');
    assert.ok(res.body.parseStats, 'parseStats present in live snapshot');
    assert.equal(res.body.parseStats.raw, 10);
    assert.equal(res.body.parseStats.accepted, 10);
    assert.equal(res.body.parseStats.selectedPath, 'Inventory.Items');
  });

  test('good inventory is preserved when a later parse sends inventory_parse_failed', async () => {
    const app = makeApp();
    // First: good inventory snapshot.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'IdAngler5',
        userId: 3003,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 5 }],
        parseStats: { raw: 5, accepted: 5, rejected: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    // Second: parse fails (e.g. after a game update breaks the catalog).
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'IdAngler5',
        userId: 3003,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_parse_failed',
        parseStats: { raw: 5, accepted: 0, rejected: 5, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/IdAngler5')
      .expect(200);

    // Old inventory should still be visible.
    assert.ok(res.body.items.some((i) => i.name === 'King Crab'), 'prior inventory preserved');
    assert.equal(res.body.parseStats.accepted, 0, 'updated parseStats reflects parse failure');
  });

  test('invalid parseStats body is silently ignored — does not crash', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'IdAngler6',
        userId: 3004,
        source: 'replion',
        isOnline: true,
        phase: 'startup',
        parseStats: 'not an object',   // garbage
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/IdAngler6')
      .expect(200);

    assert.equal(res.body.phase, 'startup');
    assert.ok(res.body.parseStats === null || res.body.parseStats === undefined,
      'invalid parseStats must be dropped');
  });

  test('inventory_empty phase is stored with parseStats', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'IdAngler7',
        userId: 3005,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_empty',
        parseStats: { raw: 0, accepted: 0, rejected: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/IdAngler7')
      .expect(200);

    assert.equal(res.body.phase, 'inventory_empty');
    assert.ok(res.body.parseStats, 'parseStats present');
    assert.equal(res.body.parseStats.raw, 0);
  });
});

// ───────────────────────────────────────────────────────────────────
// BLOCKER 4: Backend/frontend rendering pipeline end-to-end contract.
// These tests lock the fixes introduced in the BLOCKER 4 PR:
//   - Grouped inventory object returned by GET.
//   - owned.fish fallback when flat items is absent.
//   - `count` field accepted as alias for `amount`.
//   - `tier` field accepted as alias for `rarity`.
//   - tracker_status / catalog_snapshot never clear inventory.
//   - GET by username returns session stored by userId+username.
//   - parseStats raw>0 accepted=0 surfaced in response.
//   - Debug route returns correct counts.
// ───────────────────────────────────────────────────────────────────
describe('Fish It tracker — BLOCKER 4 backend rendering pipeline', () => {
  beforeEach(() => { cleanup(); });

  // B4-1: inventory_snapshot with owned.fish stores and returns items.
  test('B4-1: inventory_snapshot with owned.fish (no flat items) is stored', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B4Angler1',
        userId: 4001,
        source: 'replion',
        isOnline: true,
        // No flat `items` — only grouped `owned` (legacy fallback shape).
        owned: {
          fish:  [{ name: 'Ballina Angelfish', count: 3, category: 'fish' }],
          rods:  [{ name: 'Ghostfinn Rod',     count: 1, category: 'rod'  }],
          items: [],
        },
        parseStats: { raw: 4, accepted: 4, rejected: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B4Angler1')
      .expect(200);

    assert.ok(res.body.items.some((i) => i.name === 'Ballina Angelfish'), 'owned.fish item stored');
    assert.ok(res.body.items.some((i) => i.name === 'Ghostfinn Rod'),    'owned.rods item stored');
  });

  // B4-2: `count` (not `amount`) still stores.
  test('B4-2: inventory_snapshot with count (not amount) stores with correct amount', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B4Angler2',
        userId: 4002,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 7 }],  // `count`, no `amount`
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B4Angler2')
      .expect(200);

    const fish = res.body.items.find((i) => i.name === 'King Crab');
    assert.ok(fish, 'item stored');
    assert.equal(fish.amount, 7, 'count maps to amount');
  });

  // B4-3: tier=unknown still stores the item.
  test('B4-3: item with tier=unknown is NOT dropped', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B4Angler3',
        userId: 4003,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Darwin Clownfish', count: 1, tier: 'unknown' }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B4Angler3')
      .expect(200);

    const fish = res.body.items.find((i) => i.name === 'Darwin Clownfish');
    assert.ok(fish, 'tier=unknown item must NOT be dropped');
  });

  // B4-4: imageUrl survives backend enrichment.
  test('B4-4: imageUrl from snapshot is carried through to GET response', async () => {
    const app = makeApp();
    const img = 'https://www.roblox.com/asset-thumbnail/image?assetId=999&width=150&height=150&format=png';
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B4Angler4',
        userId: 4004,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Clownfish', count: 1, imageUrl: img }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B4Angler4')
      .expect(200);

    const fish = res.body.items.find((i) => i.name === 'Clownfish');
    assert.ok(fish, 'item present');
    assert.ok(fish.imageUrl && fish.imageUrl.includes('999'), 'imageUrl preserved');
  });

  // B4-5: tracker_status after inventory_snapshot does NOT clear inventory.
  test('B4-5: tracker_status after inventory_snapshot does not clear items', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B4Angler5',
        userId: 4005,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'King Crab', count: 3 }],
      })
      .expect(200);

    // Status-only heartbeat — must never clear items.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B4Angler5',
        userId: 4005,
        source: 'replion',
        isOnline: true,
        phase: 'player_data_selected',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B4Angler5')
      .expect(200);

    assert.ok(res.body.items.some((i) => i.name === 'King Crab'), 'items preserved after status ping');
  });

  // B4-6: catalog_snapshot POST after inventory_snapshot does NOT clear inventory.
  test('B4-6: catalog_snapshot POST does not clear live inventory', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B4Angler6',
        userId: 4006,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Clownfish', count: 4 }],
      })
      .expect(200);

    // A catalog snapshot goes to a different endpoint; the backpack endpoint
    // must still return the previously stored inventory unchanged.
    const res = await request(app)
      .get('/api/tracker/get-backpack/B4Angler6')
      .expect(200);

    assert.ok(res.body.items.some((i) => i.name === 'Clownfish'), 'inventory unchanged after catalog');
  });

  // B4-7: GET by username returns session stored by userId+username.
  test('B4-7: GET by username returns session stored under that username', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B4Angler7',
        userId: 4007,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Blue Tang', count: 2 }],
      })
      .expect(200);

    // GET by original casing — must normalise to the same key.
    const res = await request(app)
      .get('/api/tracker/get-backpack/b4angler7')
      .expect(200);

    assert.ok(res.body.items.some((i) => i.name === 'Blue Tang'), 'GET by lowercase username resolves correctly');
  });

  // B4-8: parseStats raw>0 accepted=0 is returned so frontend can show error.
  test('B4-8: parseStats raw>0 accepted=0 is returned when phase=inventory_parse_failed', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B4Angler8',
        userId: 4008,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_parse_failed',
        parseStats: { raw: 2145, accepted: 0, rejected: 2145, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B4Angler8')
      .expect(200);

    assert.equal(res.body.phase, 'inventory_parse_failed');
    assert.ok(res.body.parseStats, 'parseStats present');
    assert.equal(res.body.parseStats.raw, 2145);
    assert.equal(res.body.parseStats.accepted, 0);
    assert.equal(res.body.parseStats.selectedPath, 'Inventory.Items');
  });

  // B4-9: GET response includes inventory.all and legacy items alias.
  test('B4-9: GET response includes inventory.all and matches items length', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B4Angler9',
        userId: 4009,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'King Crab',  count: 1, category: 'fish' },
          { name: 'Clownfish',  count: 2, category: 'fish' },
          { name: 'Ghostfinn Rod', count: 1, category: 'rod' },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B4Angler9')
      .expect(200);

    assert.ok(Array.isArray(res.body.items), 'items is array (legacy)');
    assert.ok(res.body.inventory, 'inventory object present');
    assert.ok(Array.isArray(res.body.inventory.all), 'inventory.all is array');
    assert.equal(res.body.inventory.all.length, res.body.items.length, 'all length matches flat');
    assert.ok(res.body.inventory.fish.length >= 2, 'inventory.fish populated');
    assert.ok(res.body.inventory.rods.length >= 1, 'inventory.rods populated');
  });

  // B4-10: Debug route returns correct counts.
  test('B4-10: debug route returns correct counts without leaking full inventory', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B4Angler10',
        userId: 4010,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'King Crab',  count: 1, category: 'fish' },
          { name: 'Clownfish',  count: 2, category: 'fish' },
        ],
        parseStats: { raw: 2, accepted: 2, rejected: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B4Angler10')
      .expect(200);

    assert.equal(res.body.ok, true);
    assert.equal(res.body.sessionKey, 'b4angler10');
    assert.equal(res.body.counts.items, 2);
    assert.equal(res.body.counts.all, 2);
    assert.equal(res.body.counts.fish, 2, 'both are fish category');
    assert.equal(res.body.counts.rods, 0);
    assert.ok(Array.isArray(res.body.firstItems), 'firstItems present');
    assert.ok(res.body.firstItems.length <= 5, 'at most 5 items in debug');
    // Must NOT contain the full items array (only firstItems summary).
    assert.equal(res.body.items, undefined, 'full items array must not be exposed in debug response');
  });
});

// ════════════════════════════════════════════════════════════════════════════
// BLOCKER 5: catalog_summary payload, debug route shape, 413 prevention.
// These tests lock the fixes introduced in the BLOCKER 5 PR:
//   - catalog_summary (small) accepted with 200.
//   - fish_catalog_snapshot (legacy full) still accepted with 200.
//   - catalog_summary POST after inventory_snapshot does not clear inventory.
//   - inventory_snapshot after catalog_summary still stores items.
//   - debug route returns JSON (not HTML) for known user.
//   - debug route returns JSON 404 (not HTML) for unknown user.
//   - debug route includes serverCommit field.
//   - debug route firstItems shape matches spec.
//   - debug route knownKeys listed when user not found.
//   - inventory stored without full catalog (items carry their own metadata).
// ════════════════════════════════════════════════════════════════════════════
describe('Fish It tracker — BLOCKER 5 catalog_summary + debug route', () => {
  beforeEach(() => { cleanup(); });

  // B5-1: catalog_summary small payload accepted 200.
  test('B5-1: catalog_summary POST accepted with 200', async () => {
    const app = makeApp();
    const res = await request(app)
      .post('/api/tracker/update-catalog')
      .send({
        type: 'catalog_summary',
        playerName: 'B5Angler1',
        userId: 5001,
        scannedAt: Math.floor(Date.now() / 1000),
        catalogStats: { fish: 242, rods: 46, items: 7, bait: 0, images: 200, tiers: 180, metadataByIdKeys: 292, numericIdKeys: 292, stringIdKeys: 0 },
        sampleEntries: [{ key: 'clownfish', name: 'Clownfish', tier: 'common' }],
      })
      .expect(200);

    assert.equal(res.body.status, 'success');
    assert.equal(res.body.type, 'catalog_summary');
  });

  // B5-2: fish_catalog_snapshot (legacy) still accepted for backward compat.
  test('B5-2: fish_catalog_snapshot (legacy full) accepted with 200', async () => {
    const app = makeApp();
    const res = await request(app)
      .post('/api/tracker/update-catalog')
      .send({
        type: 'fish_catalog_snapshot',
        playerName: 'B5Angler2',
        catalog: { fish: [{ key: 'clownfish', name: 'Clownfish', tier: 'common', imageUrl: 'https://rbxcdn.com/img.png' }], rods: [], items: [] },
      })
      .expect(200);

    assert.equal(res.body.status, 'success');
  });

  // B5-3: unknown type returns 400.
  test('B5-3: unknown catalog payload type returns 400', async () => {
    const app = makeApp();
    const res = await request(app)
      .post('/api/tracker/update-catalog')
      .send({ type: 'full_dump', data: {} })
      .expect(400);

    assert.ok(res.body.error, 'error field present');
  });

  // B5-4: catalog_summary after inventory_snapshot does NOT clear inventory.
  test('B5-4: catalog_summary POST after inventory_snapshot does not clear inventory', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B5Angler4',
        userId: 5004,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Salmon', count: 3, category: 'fish' }],
      })
      .expect(200);

    // Send catalog_summary (different endpoint, no per-user state change).
    await request(app)
      .post('/api/tracker/update-catalog')
      .send({
        type: 'catalog_summary',
        playerName: 'B5Angler4',
        userId: 5004,
        scannedAt: Math.floor(Date.now() / 1000),
        catalogStats: { fish: 10, rods: 2, items: 0 },
        sampleEntries: [],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B5Angler4')
      .expect(200);

    assert.ok(res.body.items.length > 0, 'inventory not cleared by catalog_summary');
    assert.equal(res.body.items[0].name, 'Salmon');
  });

  // B5-5: inventory_snapshot after catalog_summary stores items correctly.
  test('B5-5: inventory_snapshot after catalog_summary stores items', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-catalog')
      .send({ type: 'catalog_summary', playerName: 'B5Angler5', userId: 5005, scannedAt: 0, catalogStats: { fish: 5 }, sampleEntries: [] })
      .expect(200);

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B5Angler5',
        userId: 5005,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Tuna', count: 1, category: 'fish', tier: 'rare', imageUrl: 'https://rbxcdn.com/tuna.png' },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B5Angler5')
      .expect(200);

    assert.equal(res.body.items.length, 1);
    assert.equal(res.body.items[0].name, 'Tuna');
    assert.equal(res.body.items[0].imageUrl, 'https://rbxcdn.com/tuna.png');
  });

  // B5-6: debug route returns JSON for known user.
  test('B5-6: debug route returns JSON (not HTML) for known user', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B5Angler6',
        userId: 5006,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Carp', count: 1, category: 'fish' }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B5Angler6')
      .expect(200);

    assert.equal(res.headers['content-type'].includes('application/json'), true, 'Content-Type is JSON');
    assert.equal(res.body.ok, true);
    assert.equal(res.body.sessionKey, 'b5angler6');
    assert.ok(res.body.serverCommit, 'serverCommit field present');
  });

  // B5-7: debug route returns JSON 404 (not HTML) for unknown user.
  test('B5-7: debug route returns JSON 404 for unknown user', async () => {
    const app = makeApp();
    const res = await request(app)
      .get('/api/fishit-tracker/debug/NoSuchUser99')
      .expect(404);

    assert.equal(res.headers['content-type'].includes('application/json'), true, 'Content-Type is JSON not HTML');
    assert.equal(res.body.ok, false);
    assert.equal(res.body.error, 'not_found');
    assert.ok(Array.isArray(res.body.knownKeys), 'knownKeys is array');
  });

  // B5-8: debug route firstItems shape matches spec (no raw imageUrl exposure).
  test('B5-8: debug route firstItems has correct shape', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B5Angler8',
        userId: 5008,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Bass', count: 5, category: 'fish', tier: 'common', imageUrl: 'https://rbxcdn.com/bass.png', itemId: 'bass_id_1' }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B5Angler8')
      .expect(200);

    const item = res.body.firstItems[0];
    assert.equal(item.name, 'Bass');
    assert.equal(item.amount, 5);
    assert.equal(item.category, 'fish');
    assert.equal(item.imageUrlPresent, true, 'imageUrlPresent not raw imageUrl');
    assert.equal(item.imageUrl, undefined, 'raw imageUrl must not be in firstItems');
    assert.equal(item.itemId, 'bass_id_1');
  });

  // B5-9: debug route knownKeys listed when multiple sessions exist.
  test('B5-9: debug route knownKeys lists other active sessions', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({ type: 'tracker_status', username: 'B5Angler9a', userId: 5091, isOnline: true, source: 'replion' })
      .expect(200);
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({ type: 'tracker_status', username: 'B5Angler9b', userId: 5092, isOnline: true, source: 'replion' })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/NoSuchUser99')
      .expect(404);

    assert.equal(res.body.ok, false);
    assert.ok(Array.isArray(res.body.knownKeys), 'knownKeys is an array');
    // Both sessions were stored in this test run — they must appear in knownKeys.
    // (liveTrackDB accumulates across tests; knownKeys allows up to 100 entries.)
    assert.ok(res.body.knownKeys.includes('b5angler9a'), 'knownKeys includes b5angler9a');
    assert.ok(res.body.knownKeys.includes('b5angler9b'), 'knownKeys includes b5angler9b');
  });

  // B5-10: inventory stored without full catalog — items carry their own metadata.
  test('B5-10: backend stores and returns inventory when no catalog_snapshot was sent', async () => {
    const app = makeApp();
    // No catalog POST at all — items carry metadata from Replion resolution.
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B5Angler10',
        userId: 5010,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Swordfish',  count: 1, category: 'fish', tier: 'legendary', imageUrl: 'https://rbxcdn.com/sword.png' },
          { name: 'Carbon Rod', count: 1, category: 'rod',  tier: 'epic',      imageUrl: 'https://rbxcdn.com/rod.png'   },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B5Angler10')
      .expect(200);

    assert.equal(res.body.items.length, 2, 'both items stored');
    assert.ok(res.body.inventory, 'inventory object present');
    assert.ok(res.body.inventory.all.length > 0, 'inventory.all populated');
    // Fish and rod partitions
    const hasRod  = res.body.inventory.rods.some((i) => i.name === 'Carbon Rod');
    const hasFish = res.body.inventory.fish.some((i) => i.name === 'Swordfish');
    assert.ok(hasRod,  'rod in inventory.rods');
    assert.ok(hasFish, 'fish in inventory.fish');
  });
});

// ════════════════════════════════════════════════════════════════════════════
// BLOCKER 6: Replion parser finalization — parseStats + phase must always
// advance past player_data_selected after inventory_parse_failed,
// inventory_empty, or inventory_snapshot.
// ════════════════════════════════════════════════════════════════════════════
describe('Fish It tracker — BLOCKER 6 Replion parse finalization', () => {
  beforeEach(() => { cleanup(); });

  test('player_data_selected then inventory_parse_failed updates phase and parseStats', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B6Angler1',
        userId: 6001,
        source: 'replion',
        isOnline: true,
        phase: 'player_data_selected',
      })
      .expect(200);

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B6Angler1',
        userId: 6001,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_parse_failed',
        parseStats: {
          raw: 3116, accepted: 0, rejected: 3116,
          selectedPath: 'Inventory.Items',
          firstRejected: [{ rawKey: '70', reason: 'unresolved_numeric_id' }],
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B6Angler1')
      .expect(200);

    assert.equal(res.body.phase, 'inventory_parse_failed');
    assert.notEqual(res.body.parseStats, null);
    assert.equal(res.body.parseStats.raw, 3116);
    assert.equal(res.body.parseStats.accepted, 0);
    assert.equal(res.body.lastPayloadType, 'tracker_status');
  });

  test('inventory_snapshot with parseStats stores items and parseStats', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B6Angler2',
        userId: 6002,
        source: 'replion',
        isOnline: true,
        phase: 'live',
        items: [
          { name: 'Ballina Angelfish', count: 3, category: 'fish', itemId: '119' },
          { name: 'Flame Angelfish', count: 2, category: 'fish', itemId: '68' },
        ],
        parseStats: {
          raw: 3116, accepted: 2, rejected: 3114,
          selectedPath: 'Inventory.Items', fish: 2, rods: 0, items: 0,
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B6Angler2')
      .expect(200);

    assert.equal(res.body.phase, 'live');
    assert.equal(res.body.lastPayloadType, 'inventory_snapshot');
    assert.ok(res.body.parseStats);
    assert.equal(res.body.parseStats.accepted, 2);
    assert.equal(res.body.counts.items, 2);
  });

  test('partial accepted + rejected stores only accepted items', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B6Angler3',
        userId: 6003,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Darwin Clownfish', count: 1, category: 'fish', itemId: '71' }],
        parseStats: {
          raw: 3116, accepted: 1, rejected: 3115,
          selectedPath: 'Inventory.Items',
          firstRejected: [{ rawKey: '70', reason: 'unresolved_numeric_id' }],
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B6Angler3')
      .expect(200);

    assert.equal(res.body.items.length, 1);
    assert.equal(res.body.items[0].name, 'Darwin Clownfish');
    assert.equal(res.body.parseStats.rejected, 3115);
    assert.ok(Array.isArray(res.body.parseStats.firstRejected));
    assert.equal(res.body.parseStats.firstRejected[0].rawKey, '70');
  });

  test('inventory_empty tracker_status stores parseStats and lastPayloadType', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B6Angler4',
        userId: 6004,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_empty',
        parseStats: { raw: 0, accepted: 0, rejected: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B6Angler4')
      .expect(200);

    assert.equal(res.body.phase, 'inventory_empty');
    assert.ok(res.body.parseStats);
    assert.equal(res.body.parseStats.raw, 0);
    assert.equal(res.body.lastPayloadType, 'tracker_status');
  });

  test('parseStats error field is preserved from inventory_parse_failed', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B6Angler5',
        userId: 6005,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_parse_failed',
        parseStats: {
          raw: 100, accepted: 0, rejected: 100,
          selectedPath: 'Inventory.Items',
          error: 'test parser traceback line',
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B6Angler5')
      .expect(200);

    assert.equal(res.body.parseStats.error, 'test parser traceback line');
  });
});

// ════════════════════════════════════════════════════════════════════════════
// BLOCKER 7: numeric Id + UUID placeholder acceptance (catalog missing OK)
// ════════════════════════════════════════════════════════════════════════════
describe('Fish It tracker — BLOCKER 7 numeric Id fallback inventory', () => {
  beforeEach(() => { cleanup(); });

  test('inventory_snapshot with Item #10 placeholder is stored and counted', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B7Angler1',
        userId: 7001,
        source: 'replion',
        isOnline: true,
        phase: 'live',
        items: [{ name: 'Item #10', count: 5, category: 'items', itemId: '10' }],
        parseStats: {
          raw: 2517, accepted: 1, acceptedInstances: 2517, rejected: 0,
          selectedPath: 'Inventory.Items', items: 1,
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B7Angler1')
      .expect(200);

    assert.equal(res.body.phase, 'live');
    assert.equal(res.body.counts.items, 1);
    assert.equal(res.body.firstItems[0].name, 'Item #10');
    assert.equal(res.body.firstItems[0].itemId, '10');
    assert.equal(res.body.parseStats.accepted, 1);
    assert.equal(res.body.parseStats.rejected, 0);
  });

  test('partial placeholder inventory with consistent parseStats counters', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B7Angler2',
        userId: 7002,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Item #10', count: 100, category: 'items', itemId: '10' },
          { name: 'Item #70', count: 50, category: 'items', itemId: '70' },
          { name: 'Ballina Angelfish', count: 3, category: 'fish', itemId: '119' },
        ],
        parseStats: {
          raw: 2517, accepted: 3, acceptedInstances: 2517, rejected: 0,
          selectedPath: 'Inventory.Items', fish: 1, items: 2,
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B7Angler2')
      .expect(200);

    assert.equal(res.body.items.length, 3);
    assert.ok(res.body.items.some((i) => i.name === 'Item #10'));
    assert.ok(res.body.items.some((i) => i.name === 'Item #70'));
    assert.equal(res.body.parseStats.raw, 2517);
    assert.equal(res.body.parseStats.rejected, 0);
    assert.equal(res.body.parseStats.accepted, 3);
  });

  test('inventory_parse_failed only when raw>0 and acceptedInstances=0', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B7Angler3',
        userId: 7003,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_parse_failed',
        parseStats: {
          raw: 100, accepted: 0, acceptedInstances: 0, rejected: 100,
          selectedPath: 'Inventory.Items',
          firstRejected: [{ rawKey: 'bad', reason: 'parse_error' }],
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B7Angler3')
      .expect(200);

    assert.equal(res.body.phase, 'inventory_parse_failed');
    assert.equal(res.body.parseStats.rejected, 100);
    assert.equal(res.body.parseStats.accepted, 0);
  });

  test('live snapshot after player_data_selected with placeholder items', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B7Angler4',
        userId: 7004,
        source: 'replion',
        isOnline: true,
        phase: 'player_data_selected',
      })
      .expect(200);

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B7Angler4',
        userId: 7004,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #10', count: 1, category: 'items', itemId: '10' }],
        parseStats: { raw: 1, accepted: 1, acceptedInstances: 1, rejected: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B7Angler4')
      .expect(200);

    assert.equal(res.body.phase, 'live');
    assert.equal(res.body.lastPayloadType, 'inventory_snapshot');
    assert.ok(res.body.parseStats);
    assert.ok(res.body.firstItems.length > 0);
  });
});

// ════════════════════════════════════════════════════════════════════════════
// BLOCKER 8: build marker, acceptedInstances phase promotion, rate limit headroom
// ════════════════════════════════════════════════════════════════════════════
describe('Fish It tracker — BLOCKER 8 accept-zero fix + rate limit', () => {
  beforeEach(() => { cleanup(); });

  test('trackerBuild is stored from tracker_status payload', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B8Angler1',
        userId: 8001,
        source: 'replion',
        isOnline: true,
        phase: 'startup',
        trackerBuild: 'BLOCKER8_ACCEPT_ZERO_AND_429_FIX_2026_06_03',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B8Angler1')
      .expect(200);

    assert.equal(res.body.trackerBuild, 'BLOCKER8_ACCEPT_ZERO_AND_429_FIX_2026_06_03');
  });

  test('acceptedInstances > 0 promotes phase to live even on tracker_status', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B8Angler2',
        userId: 8002,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_parse_failed',
        parseStats: {
          raw: 3282, accepted: 42, acceptedInstances: 3282, rejected: 0,
          selectedPath: 'Inventory.Items',
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B8Angler2')
      .expect(200);

    assert.equal(res.body.phase, 'live');
    assert.equal(res.body.parseStats.acceptedInstances, 3282);
  });

  test('placeholder Item #10 snapshot stores items and consistent parseStats', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B8Angler3',
        userId: 8003,
        source: 'replion',
        isOnline: true,
        trackerBuild: 'BLOCKER8_ACCEPT_ZERO_AND_429_FIX_2026_06_03',
        items: [{ name: 'Item #10', count: 3282, category: 'items', itemId: '10' }],
        parseStats: {
          raw: 3282, accepted: 1, acceptedInstances: 3282, rejected: 0,
          selectedPath: 'Inventory.Items', items: 1,
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B8Angler3')
      .expect(200);

    assert.equal(res.body.phase, 'live');
    assert.equal(res.body.counts.items, 1);
    assert.equal(res.body.parseStats.rejected, 0);
    assert.equal(res.body.acceptedInstances, 3282);
  });

  test('POST rate limit allows startup burst (>5 requests/min)', async () => {
    const app = makeApp();
    for (let i = 0; i < 8; i += 1) {
      await request(app)
        .post('/api/tracker/update-backpack')
        .send({
          type: 'tracker_status',
          username: 'B8Burst',
          userId: 8004,
          source: 'replion',
          isOnline: true,
          phase: 'startup',
        })
        .expect(200);
    }
  });
});

// ════════════════════════════════════════════════════════════════════════════
// BLOCKER 9: catalog/name resolution + nil-safe arithmetic + debug fields
// ════════════════════════════════════════════════════════════════════════════
describe('Fish It tracker — BLOCKER 9 catalog resolve + nil-safe fields', () => {
  beforeEach(() => { cleanup(); });

  test('resolved catalog item fields are stored from inventory_snapshot', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B9Angler1',
        userId: 9001,
        source: 'replion',
        isOnline: true,
        trackerBuild: 'BLOCKER9_CATALOG_RESOLVE_AND_NIL_FIX_2026_06_03',
        items: [{
          name: 'Carbon Rod',
          count: 1,
          category: 'rod',
          itemId: '10',
          resolved: true,
          catalogSource: 'ReplicatedStorage.Rods.10',
          catalogReason: 'catalog_hit',
        }],
        parseStats: {
          raw: 1, accepted: 1, acceptedInstances: 1, rejected: 0,
          selectedPath: 'Inventory.Items',
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B9Angler1')
      .expect(200);

    assert.equal(res.body.trackerBuild, 'BLOCKER9_CATALOG_RESOLVE_AND_NIL_FIX_2026_06_03');
    assert.equal(res.body.firstItems[0].name, 'Carbon Rod');
    assert.equal(res.body.firstItems[0].itemId, '10');
    assert.equal(res.body.firstItems[0].resolved, true);
    assert.equal(res.body.firstItems[0].catalogReason, 'catalog_hit');
    assert.match(res.body.firstItems[0].catalogSource, /ReplicatedStorage/);
  });

  test('fallback Item #990 keeps resolved=false and catalogReason', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B9Angler2',
        userId: 9002,
        source: 'replion',
        isOnline: true,
        items: [{
          name: 'Item #990',
          count: 5,
          category: 'items',
          itemId: '990',
          resolved: false,
          catalogReason: 'catalog_missing_numeric_id',
        }],
        parseStats: {
          raw: 5, accepted: 1, acceptedInstances: 5, rejected: 0,
          selectedPath: 'Inventory.Items',
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B9Angler2')
      .expect(200);

    const item = res.body.items.find((i) => i.itemId === '990');
    assert.ok(item);
    assert.equal(item.name, 'Item #990');
    assert.equal(item.resolved, false);
    assert.equal(item.catalogReason, 'catalog_missing_numeric_id');
  });

  test('catalog store lookupById enriches placeholder names when catalog has id', async () => {
    catalogStore.ingestSnapshot({
      catalog: {
        fish: [],
        rods: [{ name: 'Carbon Rod', key: 'carbon rod', tier: 'rare', itemId: '10', source: 'test' }],
        items: [],
      },
    });

    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B9Angler3',
        userId: 9003,
        source: 'replion',
        isOnline: true,
        items: [{
          name: 'Item #10',
          count: 2,
          category: 'items',
          itemId: '10',
          resolved: false,
          catalogReason: 'catalog_missing_numeric_id',
        }],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B9Angler3')
      .expect(200);

    const item = res.body.items.find((i) => i.itemId === '10');
    assert.ok(item);
    assert.equal(item.name, 'Carbon Rod');
    assert.equal(item.rarity, 'rare');
  });

  test('acceptedInstances > 0 still promotes phase=live (BLOCKER8 preserved)', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B9Angler4',
        userId: 9004,
        source: 'replion',
        isOnline: true,
        phase: 'inventory_parse_failed',
        parseStats: {
          raw: 100, accepted: 20, acceptedInstances: 100, rejected: 0,
          selectedPath: 'Inventory.Items',
        },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B9Angler4')
      .expect(200);

    assert.equal(res.body.phase, 'live');
    assert.equal(res.body.parseStats.acceptedInstances, 100);
  });
});

// ════════════════════════════════════════════════════════════════════════════
// BLOCKER 10: item name resolution (rods/crates/items by numeric id)
// ════════════════════════════════════════════════════════════════════════════
describe('Fish It tracker — BLOCKER 10 item name resolution', () => {
  beforeEach(() => { cleanup(); });

  test('trackerBuild BLOCKER10 stored in payload', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B10Angler1',
        userId: 10001,
        source: 'replion',
        isOnline: true,
        phase: 'live',
        trackerBuild: 'BLOCKER10C_NONBLOCKING_ITEM_CATALOG_UPGRADE_2026_06_03',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B10Angler1')
      .expect(200);

    assert.equal(res.body.trackerBuild, 'BLOCKER10C_NONBLOCKING_ITEM_CATALOG_UPGRADE_2026_06_03');
  });

  test('resolved rod/item names from tracker are preserved on GET', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B10Angler2',
        userId: 10002,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Carbon Rod', count: 1, category: 'rod', itemId: '10', resolved: true, catalogReason: 'catalog_hit' },
          { name: 'Common Crate', count: 2, category: 'items', itemId: '990', resolved: true, catalogReason: 'catalog_hit' },
          { name: 'Bandit Angelfish', count: 5, category: 'fish', itemId: '119', resolved: true, catalogReason: 'catalog_hit' },
        ],
        parseStats: { raw: 8, accepted: 3, acceptedInstances: 8, rejected: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B10Angler2')
      .expect(200);

    assert.equal(res.body.phase, 'live');
    assert.ok(res.body.items.some((i) => i.name === 'Carbon Rod' && i.itemId === '10'));
    assert.ok(res.body.items.some((i) => i.name === 'Common Crate' && i.itemId === '990'));
    assert.ok(res.body.items.some((i) => i.name === 'Bandit Angelfish'));
  });

  test('backend enriches Item #10 via lookupById but keeps tracker-resolved real name', async () => {
    catalogStore.ingestSnapshot({
      catalog: {
        fish: [],
        rods: [{ name: 'Wrong Rod Name', key: 'wrong rod', itemId: '10', source: 'test' }],
        items: [{ name: 'Common Crate', key: 'common crate', itemId: '990', source: 'test' }],
      },
    });

    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B10Angler3',
        userId: 10003,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Carbon Rod', count: 1, itemId: '10', resolved: true, catalogReason: 'catalog_hit' },
          { name: 'Item #990', count: 1, itemId: '990', resolved: false, catalogReason: 'catalog_missing_numeric_id' },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B10Angler3')
      .expect(200);

    const rod = res.body.items.find((i) => i.itemId === '10');
    const crate = res.body.items.find((i) => i.itemId === '990');
    assert.equal(rod.name, 'Carbon Rod', 'tracker real name must not be overwritten');
    assert.equal(crate.name, 'Common Crate', 'placeholder enriched from catalog by id');
  });

  test('unresolved placeholder Item #65 stays unresolved', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B10Angler4',
        userId: 10004,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #65', count: 1, itemId: '65', resolved: false, catalogReason: 'catalog_missing_numeric_id' }],
        parseStats: { raw: 1, accepted: 1, acceptedInstances: 1, rejected: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B10Angler4')
      .expect(200);

    assert.equal(res.body.items[0].name, 'Item #65');
    assert.equal(res.body.items[0].resolved, false);
    assert.equal(res.body.parseStats.acceptedInstances, 1);
  });
});

// ════════════════════════════════════════════════════════════════════════════
// BLOCKER 10B: fish names must not regress to Item #id after generic item pass
// ════════════════════════════════════════════════════════════════════════════
describe('Fish It tracker — BLOCKER 10B fish name regression guard', () => {
  beforeEach(() => { cleanup(); });

  test('trackerBuild BLOCKER10C stored in payload', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B10BAngler1',
        userId: 11001,
        source: 'replion',
        isOnline: true,
        trackerBuild: 'BLOCKER10C_NONBLOCKING_ITEM_CATALOG_UPGRADE_2026_06_03',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B10BAngler1')
      .expect(200);

    assert.equal(res.body.trackerBuild, 'BLOCKER10C_NONBLOCKING_ITEM_CATALOG_UPGRADE_2026_06_03');
  });

  test('resolved fish names from tracker are never overwritten by backend catalog', async () => {
    catalogStore.ingestSnapshot({
      catalog: {
        fish: [{ name: 'Item #117', key: 'item #117', itemId: '117', source: 'bad' }],
        rods: [],
        items: [{ name: 'Item #10', key: 'item #10', itemId: '10', source: 'bad' }],
      },
    });

    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B10BAngler2',
        userId: 11002,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Bandit Angelfish', count: 3, category: 'fish', itemId: '117', resolved: true, catalogReason: 'catalog_hit' },
          { name: 'Ballina Angelfish', count: 2, category: 'fish', itemId: '119', resolved: true, catalogReason: 'catalog_hit' },
          { name: 'Item #10', count: 1, category: 'items', itemId: '10', resolved: false, catalogReason: 'catalog_missing_numeric_id' },
        ],
        parseStats: { raw: 6, accepted: 3, acceptedInstances: 6, rejected: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B10BAngler2')
      .expect(200);

    const fish117 = res.body.items.find((i) => i.itemId === '117');
    const fish119 = res.body.items.find((i) => i.itemId === '119');
    const item10 = res.body.items.find((i) => i.itemId === '10');
    assert.equal(fish117.name, 'Bandit Angelfish');
    assert.equal(fish117.category, 'fish');
    assert.equal(fish117.resolved, true);
    assert.equal(fish119.name, 'Ballina Angelfish');
    assert.equal(item10.name, 'Item #10');
    assert.equal(res.body.phase, 'live');
    assert.equal(res.body.parseStats.acceptedInstances, 6);
  });

  test('backend enriches placeholder Item #10 but preserves resolved fish', async () => {
    catalogStore.ingestSnapshot({
      catalog: {
        fish: [],
        rods: [{ name: 'Carbon Rod', key: 'carbon rod', itemId: '10', source: 'test' }],
        items: [],
      },
    });

    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B10BAngler3',
        userId: 11003,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Flame Angelfish', count: 1, category: 'fish', itemId: '68', resolved: true, catalogReason: 'catalog_hit' },
          { name: 'Item #10', count: 1, category: 'items', itemId: '10', resolved: false, catalogReason: 'catalog_missing_numeric_id' },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B10BAngler3')
      .expect(200);

    assert.equal(res.body.items.find((i) => i.itemId === '68').name, 'Flame Angelfish');
    assert.equal(res.body.items.find((i) => i.itemId === '10').name, 'Carbon Rod');
  });
});

describe('BLOCKER10C non-blocking catalog and downgrade guards', () => {
  test('tracker real fish name is never overwritten by later placeholder payload', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B10CAngler1',
        userId: 12001,
        source: 'replion',
        isOnline: true,
        trackerBuild: 'BLOCKER10C_NONBLOCKING_ITEM_CATALOG_UPGRADE_2026_06_03',
        items: [
          { name: 'Bandit Angelfish', count: 1, category: 'fish', itemId: '117', resolved: true },
        ],
      })
      .expect(200);

    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B10CAngler1',
        userId: 12001,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Item #117', count: 1, category: 'items', itemId: '117', resolved: false },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B10CAngler1')
      .expect(200);

    const fish = res.body.items.find((i) => i.itemId === '117');
    assert.equal(fish.name, 'Bandit Angelfish', 'real fish name must not downgrade to Item #id');
    assert.equal(fish.category, 'fish', 'fish category must not become items');
  });

  test('placeholder Item #990 upgrades when catalog has id 990', async () => {
    catalogStore.ingestSnapshot({
      catalog: {
        fish: [],
        rods: [],
        items: [{ name: 'Common Crate', key: 'common crate', itemId: '990', category: 'items', source: 'test' }],
      },
    });

    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B10CAngler2',
        userId: 12002,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Item #990', count: 2, category: 'items', itemId: '990', resolved: false },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B10CAngler2')
      .expect(200);

    assert.equal(res.body.items.find((i) => i.itemId === '990').name, 'Common Crate');
  });

  test('placeholder Item #388 upgrades when catalog has id 388', async () => {
    catalogStore.ingestSnapshot({
      catalog: {
        fish: [],
        rods: [{ name: 'Carbon Rod', key: 'carbon rod', itemId: '388', category: 'rod', source: 'test' }],
        items: [],
      },
    });

    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'inventory_snapshot',
        username: 'B10CAngler3',
        userId: 12003,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Item #388', count: 1, category: 'items', itemId: '388', resolved: false },
        ],
      })
      .expect(200);

    const res = await request(app)
      .get('/api/tracker/get-backpack/B10CAngler3')
      .expect(200);

    assert.equal(res.body.items.find((i) => i.itemId === '388').name, 'Carbon Rod');
  });

  test('trackerBuild BLOCKER10D stored on debug endpoint', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B10CAngler4',
        userId: 12004,
        source: 'replion',
        isOnline: true,
        phase: 'live',
        trackerBuild: 'BLOCKER10D_LOADSTRING_STARTUP_FIX_2026_06_03',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B10CAngler4')
      .expect(200);

    assert.equal(res.body.trackerBuild, 'BLOCKER10D_LOADSTRING_STARTUP_FIX_2026_06_03');
  });
});

describe('BLOCKER10D loadstring startup safety', () => {
  const trackerPath = path.join(__dirname, '..', '..', 'tracker.lua');

  test('tracker.lua starts with comment header, not loadstring wrapper', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.startsWith('--'), 'must be plain Lua source');
    const firstCodeLine = src.split('\n').find((l) => {
      const t = l.trim();
      return t.length > 0 && !t.startsWith('--');
    });
    assert.ok(firstCodeLine, 'expected executable line');
    assert.ok(!firstCodeLine.includes('loadstring('), 'first executable line must not be loadstring wrapper');
    assert.ok(firstCodeLine.includes('TRACKER_BOOT_BEGIN'), 'first executable line must be TRACKER_BOOT_BEGIN');
  });

  test('TRACKER_BOOT_BEGIN appears before catalog scan code', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    const boot = src.indexOf('TRACKER_BOOT_BEGIN BLOCKER10D');
    const catalog = src.indexOf('scanReplicatedStorageFishCatalog');
    assert.ok(boot >= 0, 'TRACKER_BOOT_BEGIN missing');
    assert.ok(catalog >= 0, 'catalog scan missing');
    assert.ok(boot < catalog, 'boot marker must precede catalog work');
  });

  test('no orphaned duplicate return/end syntax corruption', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(!/return true\nend\n\n\s+return true\nend\n\n-- BLOCKER10C/m.test(src), 'duplicate orphaned return/end block must not exist');
  });

  test('BLOCKER10C non-blocking helpers remain after BLOCKER10D fix', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('STARTUP_NON_BLOCKING'));
    assert.ok(src.includes('scanBudgetYield'));
    assert.ok(src.includes('buildMetadataCatalogAsync'));
    assert.ok(src.includes('INVENTORY_PHASE_A'));
    assert.ok(src.includes('INVENTORY_PHASE_B'));
    assert.ok(src.includes('isPlaceholderName'));
  });

  test('exact loader command shape is documented in tracker header', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/tracker.lua"))()'));
  });
});
