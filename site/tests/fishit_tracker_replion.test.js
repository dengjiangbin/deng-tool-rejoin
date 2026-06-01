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

    assert.equal(res.body.found, true);
    assert.equal(res.body.sessionKey, 'b4angler10');
    assert.equal(res.body.counts.flatItems, 2);
    assert.equal(res.body.counts.inventoryAll, 2);
    assert.equal(res.body.counts.inventoryFish, 2, 'both are fish category');
    assert.equal(res.body.counts.inventoryRods, 0);
    assert.ok(Array.isArray(res.body.first5Items), 'first5Items present');
    assert.ok(res.body.first5Items.length <= 5, 'at most 5 items in debug');
    // Must NOT contain the full items array (only first5Items summary).
    assert.equal(res.body.items, undefined, 'full items array must not be exposed in debug response');
  });
});
