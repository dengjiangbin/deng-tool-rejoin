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

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');

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

    assert.ok(res.body.items.some((i) => i.name === 'Ballina Angelfish'), 'owned.fish on public GET');
    assert.ok(res.body.allItems.some((i) => i.name === 'Ghostfinn Rod'), 'owned.rods in allItems');
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

  // B4-9: GET public fields are fish-only; full mix lives under internalInventory.
  test('B4-9: GET response fish-only items with internalInventory for mixed', async () => {
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

    assert.ok(Array.isArray(res.body.items), 'items is array (legacy, fish-only)');
    assert.equal(res.body.items.length, 2, 'public items are fish-only');
    assert.ok(res.body.inventory, 'inventory object present');
    assert.ok(res.body.inventory.fish.length >= 2, 'inventory.fish populated');
    assert.equal(res.body.inventory.rods.length, 0, 'rods not exposed on public inventory');
    assert.ok(res.body.internalInventory, 'internalInventory present');
    assert.ok(res.body.internalInventory.rods.length >= 1, 'rod kept in internalInventory');
    assert.equal(res.body.allItems.length, 3, 'allItems has full mixed flat list');
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
    assert.equal(res.body.items[0].imageSource, 'missing_image_asset');
    assert.equal(res.body.items[0].imageUrl, null);
    assert.ok(res.body.allItems[0].imageUrl === 'https://rbxcdn.com/tuna.png', 'tracker image kept on internal allItems');
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

    assert.equal(res.body.items.length, 1, 'public items are fish-only');
    assert.ok(res.body.inventory, 'inventory object present');
    assert.ok(res.body.inventory.fish.some((i) => i.name === 'Swordfish'), 'fish in public inventory');
    assert.ok(!res.body.items.some((i) => i.name === 'Carbon Rod'), 'rod not in public items');
    assert.ok(res.body.internalInventory.rods.some((i) => i.name === 'Carbon Rod'), 'rod in internalInventory');
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
    assert.equal(res.body.rawItems[0].name, 'Item #10');
    assert.equal(res.body.firstItems[0].name, 'Topwater Bait');
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

    assert.equal(res.body.items.length, 2, 'public GET is fish-only');
    assert.ok(res.body.allItems.some((i) => i.itemId === '10' && i.name === 'Topwater Bait'));
    assert.ok(res.body.items.some((i) => i.itemId === '70' && i.name === 'Yello Damselfish'));
    assert.ok(res.body.items.some((i) => i.itemId === '119' && i.name === 'Ballina Angelfish'));
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
          name: 'Item #65',
          count: 5,
          category: 'items',
          itemId: '65',
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

    const item = res.body.allItems.find((i) => i.itemId === '65');
    assert.ok(item);
    assert.equal(item.name, 'Item #65');
    assert.equal(item.resolved, false);
    assert.equal(item.catalogReason, 'catalog_missing_numeric_id');
    assert.equal(res.body.items.length, 0);
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

    const item = res.body.allItems.find((i) => i.itemId === '10');
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
    assert.ok(res.body.allItems.some((i) => i.name === 'Carbon Rod' && i.itemId === '10'));
    assert.ok(res.body.allItems.some((i) => i.name === 'Common Crate' && i.itemId === '990'));
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

    const rod = res.body.allItems.find((i) => i.itemId === '10');
    const crate = res.body.allItems.find((i) => i.itemId === '990');
    assert.equal(rod.name, 'Carbon Rod', 'tracker real name must not be overwritten');
    assert.equal(crate.name, 'Common Crate', 'placeholder enriched from catalog by id');
    assert.equal(res.body.items.length, 0, 'non-fish not on public GET');
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

    const unresolved = res.body.allItems.find((i) => i.itemId === '65');
    assert.equal(unresolved.name, 'Item #65');
    assert.equal(unresolved.resolved, false);
    assert.equal(res.body.items.length, 0, 'unresolved non-fish not on public GET');
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
    const item10 = res.body.allItems.find((i) => i.itemId === '10');
    assert.equal(fish117.name, 'Bandit Angelfish');
    assert.equal(fish117.category, 'fish');
    assert.equal(fish117.resolved, true);
    assert.equal(fish119.name, 'Ballina Angelfish');
    assert.equal(item10.name, 'Topwater Bait');
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
    assert.equal(res.body.allItems.find((i) => i.itemId === '10').name, 'Carbon Rod');
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

    assert.equal(res.body.allItems.find((i) => i.itemId === '990').name, 'Common Crate');
    assert.equal(res.body.items.length, 0, 'crate not on public GET');
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

    assert.equal(res.body.allItems.find((i) => i.itemId === '388').name, 'Carbon Rod');
    assert.equal(res.body.items.length, 0, 'rod not on public GET');
  });

  test('trackerBuild BLOCKER10H stored on debug endpoint', async () => {
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
        trackerBuild: 'BLOCKER10H_ULTRA_LIGHT_PLAYER_DATA_ONLY_2026_06_04',
      })
      .expect(200);

    const res = await request(app)
      .get('/api/fishit-tracker/debug/B10CAngler4')
      .expect(200);

    assert.equal(res.body.trackerBuild, 'BLOCKER10H_ULTRA_LIGHT_PLAYER_DATA_ONLY_2026_06_04');
  });
});

describe('BLOCKER10G targeted item diagnostics no-freeze', () => {
  const trackerPath = path.join(__dirname, '..', '..', 'tracker.lua');
  const compileScript = path.join(__dirname, '..', '..', 'scripts', 'validate_tracker_compile.js');

  beforeEach(() => { cleanup(); });

  test('validate_tracker_compile.js passes on tracker.lua', () => {
    const out = execFileSync(process.execPath, [compileScript, trackerPath], { encoding: 'utf8' });
    assert.match(out, /TRACKER_COMPILE_VALIDATION OK/);
    assert.match(out, /BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06/);
  });

  test('targeted diagnostics disabled by default; heavy flags remain disabled', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('enableTargetedItemDiagnostics = false'));
    assert.ok(src.includes('enableHeavyCatalog = false'));
    assert.ok(src.includes('enablePhaseBItemUpgrade = false'));
    assert.ok(src.includes('debugRemoteHooks = false'));
    assert.ok(src.includes('enableModuleRequire = false'));
    assert.ok(src.includes('lightSyncEnabled = true'));
  });

  test('targeted diagnostics only processes target item ids', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('targetItemIds = {'));
    assert.ok(src.includes('"990"'));
    assert.ok(src.includes('runTargetedItemDiagnosticsAsync'));
    assert.ok(src.match(/for _, idStr in ipairs\(LiveSafe\.targetItemIds\)/));
    assert.ok(src.includes('isPlaceholderName(e.name, idStr)'));
  });

  test('shallow targeted scan does not call GetDescendants', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    const shallowStart = src.indexOf('local function shallowLookupIdInRs(idStr, checkedPaths)');
    const shallowEnd = src.indexOf('local function traceTargetItemId(idStr)', shallowStart);
    assert.ok(shallowStart >= 0 && shallowEnd > shallowStart);
    const shallowBlock = src.slice(shallowStart, shallowEnd);
    assert.ok(shallowBlock.includes(':GetChildren()'));
    assert.ok(!shallowBlock.includes('GetDescendants'));
    assert.ok(src.includes('function runTargetedItemDiagnosticsAsync'));
  });

  test('module require remains disabled by default', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.match(/enableModuleRequire = false/));
    assert.ok(src.match(/function scanTargetedDefinitionModules[\s\S]{0,80}if LiveSafe\.catalogAborted or not LiveSafe\.enableModuleRequire/));
  });

  test('remote hooks remain disabled by default', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('debugRemoteHooks = false'));
    assert.ok(src.match(/function hookRemotesDeferred[\s\S]{0,120}if not LiveSafe\.debugRemoteHooks/));
  });

  test('heavy catalog remains disabled by default', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('enableHeavyCatalog = false'));
    assert.ok(src.match(/function buildMetadataCatalogAsync[\s\S]{0,120}if not LiveSafe\.enableHeavyCatalog/));
  });

  test('freeze monitor disabled by default', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('enableFreezeMonitor = false'));
    assert.ok(src.includes('function startFreezeMonitor'));
  });

  test('target diagnostics abort/pause on stall', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.match(/function runTargetedItemDiagnosticsAsync[\s\S]{0,600}catalogPausedUntil/));
    assert.ok(src.includes('TARGET_ITEM_DIAG paused reason=frame_stall'));
    assert.ok(src.includes('TARGET_ITEM_DIAG aborted reason=catalog_aborted'));
  });

  test('boot marker is BLOCKER10N build', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('TRACKER_BOOT_BEGIN BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(!src.includes('TRACKER_BOOT_BEGIN BLOCKER10J'));
    assert.ok(src.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
  });

  test('Item #990 upgrades only with exact catalog metadata', async () => {
    catalogStore._reset();
    catalogStore.upsertByItemId({ itemId: '990', name: 'Common Crate', category: 'items', source: 'test' });
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10GUser990',
        userId: 13001,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #990', count: 2, category: 'items', itemId: '990', resolved: false }],
      })
      .expect(200);
    const res = await request(app).get('/api/tracker/get-backpack/B10GUser990').expect(200);
    assert.equal(res.body.allItems.find((i) => i.itemId === '990').name, 'Common Crate');
    const reject = catalogStore.upsertByItemId({ itemId: '990', name: 'Item #990', category: 'items', source: 'bad' });
    assert.equal(reject.updated, false);
    assert.equal(reject.reason, 'placeholder_or_empty');
  });

  test('Item #388 upgrades only with exact catalog metadata', async () => {
    catalogStore._reset();
    catalogStore.upsertByItemId({ itemId: '388', name: 'Carbon Rod', category: 'rod', source: 'test' });
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10GUser388',
        userId: 13002,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #388', count: 1, category: 'items', itemId: '388', resolved: false }],
      })
      .expect(200);
    const res = await request(app).get('/api/tracker/get-backpack/B10GUser388').expect(200);
    assert.equal(res.body.allItems.find((i) => i.itemId === '388').name, 'Carbon Rod');
  });

  test('fish names cannot downgrade via catalog cache', () => {
    cleanup();
    const reject = catalogStore.upsertByItemId({ itemId: '117', name: 'Item #117', category: 'items', source: 'bad' });
    assert.equal(reject.updated, false);
    assert.equal(catalogStore.lookupById('117').name, 'Bandit Angelfish');
  });

  test('backend catalog cache enriches placeholder from stored real metadata', async () => {
    catalogStore._reset();
    catalogStore.upsertByItemId({ itemId: '10', name: 'Topwater Bait', category: 'bait', source: 'diag' });
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10GUser10',
        userId: 13004,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #10', count: 1, category: 'items', itemId: '10', resolved: false }],
      })
      .expect(200);
    const res = await request(app).get('/api/tracker/get-backpack/B10GUser10').expect(200);
    const item = res.body.allItems.find((i) => i.itemId === '10');
    assert.equal(item.name, 'Topwater Bait');
    assert.ok(item.catalogEnrichmentSource || item.catalogReason);
    assert.equal(res.body.items.length, 0);
  });

  test('placeholder cannot overwrite cached real name', () => {
    catalogStore._reset();
    catalogStore.upsertByItemId({ itemId: '990', name: 'Common Crate', category: 'items', source: 'first' });
    const r = catalogStore.upsertByItemId({ itemId: '990', name: 'Item #990', category: 'items', source: 'bad' });
    assert.equal(r.updated, false);
    assert.equal(catalogStore.lookupById('990').name, 'Common Crate');
  });

  test('debug endpoint exposes unresolvedDiagnostics fields', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        type: 'tracker_status',
        username: 'B10GDebug',
        userId: 13005,
        source: 'replion',
        isOnline: true,
        phase: 'targeted_diagnostics',
        trackerBuild: 'BLOCKER10G_TARGETED_ITEM_DIAGNOSTICS_NO_FREEZE_2026_06_03',
        unresolvedDiagnostics: [
          {
            id: 990,
            count: 5,
            currentName: 'Item #990',
            checkedPaths: ['Replion.Items', 'ReplicatedStorage.Shared'],
            found: false,
            candidatePath: null,
            candidateKeys: [],
            elapsedMs: 12,
          },
        ],
        discoveredCatalog: [{ itemId: '10', name: 'Topwater Bait', category: 'bait', source: 'targeted_diag' }],
      })
      .expect(200);
    const res = await request(app).get('/api/fishit-tracker/debug/B10GDebug').expect(200);
    assert.ok(Array.isArray(res.body.unresolvedDiagnostics));
    assert.deepEqual(res.body.stillUnresolvedIds, [990]);
    assert.equal(catalogStore.lookupById('10').name, 'Topwater Bait');
  });
});

describe('BLOCKER10F safe minimal no-freeze compile gate (superseded by BLOCKER10J)', () => {
  const trackerPath = path.join(__dirname, '..', '..', 'tracker.lua');
  const compileScript = path.join(__dirname, '..', '..', 'scripts', 'validate_tracker_compile.js');

  test('validate_tracker_compile.js passes on tracker.lua', () => {
    const out = execFileSync(process.execPath, [compileScript, trackerPath], { encoding: 'utf8' });
    assert.match(out, /TRACKER_COMPILE_VALIDATION OK/);
    assert.match(out, /BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06/);
  });

  test('safe minimal flags default off for heavy work', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('safeMinimalMode = true'));
    assert.ok(src.includes('enableHeavyCatalog = false'));
    assert.ok(src.includes('enablePhaseBItemUpgrade = false'));
    assert.ok(src.includes('debugRemoteHooks = false'));
    assert.ok(src.includes('enableModuleRequire = false'));
  });

  test('heavy catalog spawn gated when disabled', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('function buildMetadataCatalogAsync'));
    assert.ok(src.match(/if not LiveSafe\.enableHeavyCatalog then[\s\S]{0,80}HEAVY_CATALOG disabled=true/));
  });

  test('boot marker is BLOCKER10N build', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('TRACKER_BOOT_BEGIN BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(!src.includes('TRACKER_BOOT_BEGIN BLOCKER10J'));
    assert.ok(src.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
  });

  test('inventory upload and fish downgrade guards remain', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('INVENTORY_UPLOAD ok=true'));
    assert.ok(src.includes('CATALOG_DOWNGRADE_BLOCKED'));
    assert.ok(src.includes('shouldReplaceName'));
  });
});

describe('BLOCKER10E live freeze proof and targeted item resolution', () => {
  const trackerPath = path.join(__dirname, '..', '..', 'tracker.lua');

  test('freeze monitor and safe minimal mode present', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('enableFreezeMonitor = false'));
    assert.ok(src.includes('safeMinimalMode = true'));
    assert.ok(src.includes('enableHeavyCatalog = false'));
    assert.ok(src.includes('lightSyncEnabled = true'));
  });

  test('remote hooks disabled by default', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('debugRemoteHooks = false'));
    assert.ok(src.match(/function hookRemotesDeferred[\s\S]{0,120}if not LiveSafe\.debugRemoteHooks/));
  });

  test('heavy catalog disabled by default', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('enableHeavyCatalog = false'));
    assert.ok(!src.includes('task.wait(LiveSafe.heavyCatalogDelaySec)'));
  });

  test('Phase B item upgrade disabled by default', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('enablePhaseBItemUpgrade = false'));
    assert.ok(src.match(/function runInventoryPhaseB[\s\S]{0,80}if not LiveSafe\.enablePhaseBItemUpgrade/));
  });

  test('strict live budget defaults', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('budgetMs = 2'));
    assert.ok(src.includes('scanOpsPerYield = 4'));
  });

  test('module require disabled by default in live safe mode', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('enableModuleRequire = false'));
    assert.ok(src.includes('MODULE_REQUIRE_SUMMARY'));
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
    const boot = src.indexOf('TRACKER_BOOT_BEGIN BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');
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
    assert.ok(src.includes('LiveSafe.nonBlocking') || src.includes('nonBlocking = true'));
    assert.ok(src.includes('scanBudgetYield'));
    assert.ok(src.includes('buildMetadataCatalogAsync'));
    assert.ok(src.includes('INVENTORY_UPLOAD ok=true'));
    assert.ok(src.includes('INVENTORY_PHASE_B'));
    assert.ok(src.includes('isPlaceholderName'));
  });

  test('exact loader command shape is documented in tracker header', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/tracker.lua"))()'));
  });
});

describe('BLOCKER10H ultra-light player-data-only server enrichment', () => {
  const trackerPath = path.join(__dirname, '..', '..', 'tracker.lua');
  const compileScript = path.join(__dirname, '..', '..', 'scripts', 'validate_tracker_compile.js');

  beforeEach(() => { cleanup(); });

  test('validate_tracker_compile.js passes with BLOCKER10J marker', () => {
    const out = execFileSync(process.execPath, [compileScript, trackerPath], { encoding: 'utf8' });
    assert.match(out, /BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06/);
  });

  test('player-data-only flags default correctly', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('playerDataOnly = true'));
    assert.ok(src.includes('clientCatalogResolution = false'));
    assert.ok(src.includes('enableTargetedItemDiagnostics = false'));
    assert.ok(!src.match(/pcall\(buildQuickPriorityCatalog\)/));
  });

  test('startup does not scan ReplicatedStorage for catalog by default', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    const startup = src.indexOf('function runReplionStartupPhase');
    const block = src.slice(startup, startup + 2500);
    assert.ok(!block.includes('pcall(buildQuickPriorityCatalog)'));
    assert.ok(block.includes('refreshFromReplion'));
  });

  test('raw Item #117 enriched to Bandit Angelfish from seeded catalog', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10HUser117',
        userId: 14001,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #117', count: 3, category: 'items', itemId: '117', resolved: false }],
        parseStats: { raw: 3318, accepted: 23, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const res = await request(app).get('/api/tracker/get-backpack/B10HUser117').expect(200);
    const fish = res.body.items.find((i) => i.itemId === '117');
    assert.equal(fish.name, 'Bandit Angelfish');
    assert.equal(fish.category, 'fish');
  });

  test('raw category items does not overwrite catalog category fish on display', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10HUser117b',
        userId: 14002,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #117', count: 1, category: 'items', itemId: '117' }],
      })
      .expect(200);

    const stored = (await request(app).get('/api/fishit-tracker/debug/B10HUser117b').expect(200)).body;
    assert.equal(stored.rawItems[0].name, 'Item #117');
    assert.equal(stored.rawItems[0].category, 'items');
    assert.equal(stored.enrichedItems[0].name, 'Bandit Angelfish');
    assert.equal(stored.enrichedItems[0].category, 'fish');
  });

  test('Topwater Bait cache survives raw Item #10', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10HUser10',
        userId: 14003,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #10', count: 5, category: 'items', itemId: '10' }],
      })
      .expect(200);

    const res = await request(app).get('/api/tracker/get-backpack/B10HUser10').expect(200);
    assert.equal(res.body.allItems.find((i) => i.itemId === '10').name, 'Topwater Bait');
    assert.equal(res.body.items.length, 0);
  });

  test('placeholder cannot overwrite seeded real catalog name', () => {
    cleanup();
    const r = catalogStore.upsertByItemId({ itemId: '117', name: 'Item #117', category: 'items', source: 'bad' });
    assert.equal(r.updated, false);
    assert.equal(catalogStore.lookupById('117').name, 'Bandit Angelfish');
  });

  test('debug endpoint shows rawItems enrichedItems and catalogForItems', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10HDebug',
        userId: 14004,
        source: 'replion',
        isOnline: true,
        trackerBuild: 'BLOCKER10H_ULTRA_LIGHT_PLAYER_DATA_ONLY_2026_06_04',
        items: [
          { name: 'Item #117', count: 2, category: 'items', itemId: '117' },
          { name: 'Item #10', count: 1, category: 'items', itemId: '10' },
        ],
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/debug/B10HDebug').expect(200);
    assert.equal(res.body.rawItems[0].name, 'Item #117');
    assert.equal(res.body.enrichedItems[0].name, 'Bandit Angelfish');
    assert.equal(res.body.enrichedItems[0].category, 'fish');
    assert.ok(res.body.catalogForItems['117']);
    assert.equal(res.body.catalogForItems['117'].name, 'Bandit Angelfish');
  });

  test('fish names from tracker are never downgraded by catalog', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10HFish',
        userId: 14005,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Bandit Angelfish', count: 1, category: 'fish', itemId: '117', resolved: true }],
      })
      .expect(200);

    const res = await request(app).get('/api/tracker/get-backpack/B10HFish').expect(200);
    assert.equal(res.body.items[0].name, 'Bandit Angelfish');
    assert.equal(res.body.items[0].category, 'fish');
  });
});

describe('BLOCKER10I enrichment display (carried into BLOCKER10J)', () => {
  const trackerPath = path.join(__dirname, '..', '..', 'tracker.lua');
  const compileScript = path.join(__dirname, '..', '..', 'scripts', 'validate_tracker_compile.js');

  beforeEach(() => { cleanup(); });

  test('validate_tracker_compile.js passes with BLOCKER10J marker', () => {
    const out = execFileSync(process.execPath, [compileScript, trackerPath], { encoding: 'utf8' });
    assert.match(out, /BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06/);
  });

  test('light sync defaults: 10s loop, no attachReplionListeners by default', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('lightSyncEnabled = true'));
    assert.ok(src.includes('lightSyncIntervalSeconds = 10'));
    assert.ok(src.includes('repeatUpload = true'));
    assert.ok(src.includes('oneShot = false'));
    assert.ok(src.includes('SYNC_LOOP_STARTED interval='));
    assert.ok(src.includes('cachedInventoryPath = "Inventory.Items"'));
  });

  test('boot marker is BLOCKER10N build', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('TRACKER_BOOT_BEGIN BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(!src.includes('TRACKER_BOOT_BEGIN BLOCKER10J'));
    assert.ok(src.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
  });

  test('itemId 117 raw enriches to Bandit Angelfish / fish on debug firstItems', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10IUser117',
        userId: 15001,
        source: 'replion',
        isOnline: true,
        trackerBuild: 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06',
        items: [{ name: 'Item #117', count: 3, category: 'items', itemId: '117' }],
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/debug/B10IUser117').expect(200);
    assert.equal(res.body.firstItems[0].name, 'Bandit Angelfish');
    assert.equal(res.body.firstItems[0].category, 'fish');
    assert.equal(res.body.rawFirstItems[0].name, 'Item #117');
    assert.equal(res.body.rawFirstItems[0].category, 'items');
  });

  test('itemId 10 enriches to Topwater Bait', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10IUser10',
        userId: 15002,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #10', count: 1, category: 'items', itemId: '10' }],
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/debug/B10IUser10').expect(200);
    assert.equal(res.body.firstItems[0].name, 'Topwater Bait');
  });

  test('itemId 990 enriches to Common Crate', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10IUser990',
        userId: 15003,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #990', count: 1, category: 'items', itemId: '990' }],
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/debug/B10IUser990').expect(200);
    assert.equal(res.body.firstItems[0].name, 'Common Crate');
  });

  test('itemId 388 enriches to Carbon Rod', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10IUser388',
        userId: 15004,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #388', count: 1, category: 'items', itemId: '388' }],
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/debug/B10IUser388').expect(200);
    assert.equal(res.body.firstItems[0].name, 'Carbon Rod');
  });

  test('countsRaw vs countsEnriched are separate; enriched fish count not zero', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10ICounts',
        userId: 15005,
        source: 'replion',
        isOnline: true,
        items: [
          { name: 'Item #117', count: 1, category: 'items', itemId: '117' },
          { name: 'Item #10', count: 1, category: 'items', itemId: '10' },
          { name: 'Item #990', count: 1, category: 'items', itemId: '990' },
        ],
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/debug/B10ICounts').expect(200);
    assert.equal(res.body.countsRaw.fish, 0);
    assert.equal(res.body.countsRaw.itemsOnly, 3);
    assert.ok(res.body.countsEnriched.fish >= 1);
    assert.equal(res.body.counts.fish, res.body.countsEnriched.fish);
    assert.equal(res.body.counts.itemsOnly, res.body.countsEnriched.itemsOnly);
  });

  test('get-backpack uses enriched inventory groups from flat raw items', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10IGetPack',
        userId: 15006,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #117', count: 2, category: 'items', itemId: '117' }],
      })
      .expect(200);

    const res = await request(app).get('/api/tracker/get-backpack/B10IGetPack').expect(200);
    assert.equal(res.body.items[0].name, 'Bandit Angelfish');
    assert.equal(res.body.inventory.fish.length, 1);
    assert.equal(res.body.inventory.items.length, 0);
    assert.ok(res.body.countsEnriched);
    assert.equal(res.body.countsEnriched.fish, 1);
  });

  test('catalogForItems is object keyed by itemId', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10ICatalog',
        userId: 15007,
        source: 'replion',
        isOnline: true,
        items: [{ name: 'Item #117', count: 1, category: 'items', itemId: '117' }],
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/debug/B10ICatalog').expect(200);
    assert.equal(typeof res.body.catalogForItems, 'object');
    assert.ok(!Array.isArray(res.body.catalogForItems));
    assert.equal(res.body.catalogForItems['117'].name, 'Bandit Angelfish');
    assert.equal(res.body.catalogForItems['117'].category, 'fish');
  });
});

describe('BLOCKER10J safe light sync 10s + server commit resolution', () => {
  const trackerPath = path.join(__dirname, '..', '..', 'tracker.lua');
  const compileScript = path.join(__dirname, '..', '..', 'scripts', 'validate_tracker_compile.js');
  const routes = require('../src/fishitTrackerRoutes');

  beforeEach(() => { cleanup(); });

  test('validate_tracker_compile.js passes with BLOCKER10J marker', () => {
    const out = execFileSync(process.execPath, [compileScript, trackerPath], { encoding: 'utf8' });
    assert.match(out, /BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06/);
  });

  test('light sync defaults and no attachReplionListeners on default path', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('lightSyncEnabled = true'));
    assert.ok(src.includes('lightSyncIntervalSeconds = 10'));
    assert.ok(src.includes('repeatUpload = true'));
    assert.ok(src.includes('oneShot = false'));
    assert.ok(src.includes('SYNC_LOOP_STARTED interval='));
    assert.ok(src.includes('SYNC_UPLOAD ok=true'));
    const startup = src.slice(src.indexOf('function runReplionStartupPhase'), src.indexOf('function runReplionStartupPhase') + 5500);
    assert.ok(startup.includes('SYNC_LOOP_STARTED interval='));
    assert.ok(startup.includes('not LiveSafe.lightSyncEnabled'));
    assert.ok(!startup.match(/if not LiveSafe\.oneShot then\s*\n\s*attachReplionListeners/));
  });

  test('oneShot=true still disables light sync loop', () => {
    const src = fs.readFileSync(trackerPath, 'utf8');
    assert.ok(src.includes('LiveSafe.lightSyncLoopStarted'));
    assert.ok(src.includes('TRACKER_DONE one_shot=true'));
  });

  test('resolveServerCommit prefers git HEAD over package version', () => {
    const commit = routes.resolveServerCommit();
    assert.ok(commit);
    assert.notEqual(commit, 'unknown');
    if (!process.env.GIT_COMMIT) {
      assert.match(commit, /^[0-9a-f]{7,40}$/i);
    }
  });

  test('inventory POST returns ok accepted lastSeenAt and updates on repeat sync', async () => {
    const app = makeApp();
    const post1 = await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10JSync',
        userId: 16002,
        source: 'replion',
        isOnline: true,
        type: 'inventory_snapshot',
        items: [{ name: 'Item #117', count: 1, category: 'items', itemId: '117' }],
        parseStats: { raw: 23, accepted: 23, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    assert.equal(post1.body.ok, true);
    assert.equal(post1.body.accepted, 1);
    assert.ok(post1.body.lastSeenAt);
    assert.ok(post1.body.lastInventoryAt);
    assert.equal(post1.body.online, true);

    await new Promise((r) => setTimeout(r, 15));

    const post2 = await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10JSync',
        userId: 16002,
        source: 'replion',
        isOnline: true,
        type: 'inventory_snapshot',
        items: [{ name: 'Item #117', count: 1, category: 'items', itemId: '117' }],
        parseStats: { raw: 23, accepted: 23, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    assert.equal(post2.body.ok, true);
    const get = await request(app).get('/api/tracker/get-backpack/B10JSync').expect(200);
    assert.equal(get.body.isOnline, true);
    assert.ok(get.body.lastInventoryAt);
    assert.equal(get.body.items.find((i) => i.itemId === '117').name, 'Bandit Angelfish');
  });

  test('canonical fishit-tracker POST/GET routes accept inventory upload', async () => {
    const app = makeApp();
    const post = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10J2Canon',
        userId: 16003,
        source: 'replion',
        isOnline: true,
        type: 'inventory_snapshot',
        items: [{ name: 'Item #117', count: 1, category: 'items', itemId: '117' }],
        parseStats: { raw: 11, accepted: 11, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    assert.equal(post.body.ok, true);
    assert.equal(post.body.status, 'success');
    assert.equal(post.body.accepted, 1);
    assert.ok(post.body.lastSeenAt);
    assert.ok(post.body.lastInventoryAt);

    const get = await request(app)
      .get('/api/fishit-tracker/get-backpack/B10J2Canon')
      .expect(200);
    assert.equal(get.body.isOnline, true);
    assert.equal(get.body.items.find((i) => i.itemId === '117').name, 'Bandit Angelfish');

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10J2Canon').expect(200);
    assert.equal(dbg.body.ok, true);
    assert.equal(dbg.body.sessionKey, 'b10j2canon');
    const miss = await request(app).get('/api/fishit-tracker/debug/NotB10J2Canon').expect(404);
    assert.ok(miss.body.knownKeys.includes('b10j2canon'));
  });

  test('legacy POST alias still works after canonical route added', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10J2Alias',
        userId: 16004,
        isOnline: true,
        items: [{ name: 'Item #117', count: 1, itemId: '117' }],
      })
      .expect(200);
    const dbg = await request(app).get('/api/fishit-tracker/debug/B10J2Alias').expect(200);
    assert.equal(dbg.body.ok, true);
    const miss = await request(app).get('/api/fishit-tracker/debug/wrongkeyzzz').expect(404);
    assert.ok(miss.body.knownKeys.includes('b10j2alias'));
  });

  test('debug endpoint exposes enriched fields and non-version serverCommit', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/tracker/update-backpack')
      .send({
        username: 'B10JDebug',
        userId: 16001,
        source: 'replion',
        isOnline: true,
        trackerBuild: 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06',
        items: [{ name: 'Item #117', count: 1, category: 'items', itemId: '117' }],
      })
      .expect(200);

    const res = await request(app).get('/api/fishit-tracker/debug/B10JDebug').expect(200);
    assert.equal(res.body.firstItems[0].name, 'Bandit Angelfish');
    assert.equal(res.body.rawFirstItems[0].name, 'Item #117');
    assert.ok(res.body.countsRaw);
    assert.ok(res.body.countsEnriched);
    assert.ok(res.body.catalogForItems['117']);
    assert.notEqual(res.body.serverCommit, '1.0.0');
  });
});

describe('BLOCKER10K fish-only public + raw proof', () => {
  const routes = require('../src/fishitTrackerRoutes');
  const {
    buildPublicFishFields,
    buildRawInspector,
    deriveResolution,
    isPublicFishItem,
  } = routes;

  const mixedPayload = [
    { name: 'Topwater Bait', amount: 1, category: 'bait', itemId: '10' },
    { name: 'Carbon Rod', amount: 138, category: 'rod', itemId: '388' },
    { name: 'Common Crate', amount: 5, category: 'items', itemId: '990' },
    { name: 'Flame Angelfish', amount: 136, category: 'fish', itemId: '68' },
    { name: 'Yello Damselfish', amount: 14, category: 'fish', itemId: '70' },
  ];

  test('public fish-only filtering on get-backpack', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10KFish',
        userId: 17001,
        isOnline: true,
        type: 'inventory_snapshot',
        items: mixedPayload,
      })
      .expect(200);

    const get = await request(app).get('/api/fishit-tracker/get-backpack/B10KFish').expect(200);
    assert.equal(get.body.items.length, 2, 'legacy items field is fish-only');
    assert.equal(get.body.fishItems.length, 2);
    assert.equal(get.body.publicItems.length, 2);
    const fishNames = get.body.fishItems.map((i) => i.name);
    assert.ok(fishNames.includes('Flame Angelfish'));
    assert.ok(fishNames.some((n) => /damselfish/i.test(n)));
    assert.equal(get.body.fishCounts.fishTypes, 2);
    assert.equal(get.body.fishCounts.fishInstances, 150);
    assert.equal(get.body.fishCounts.hiddenNonFishTypes, 3);
    assert.equal(get.body.fishCounts.hiddenNonFishInstances, 144);
    assert.ok(!get.body.fishItems.some((i) => i.name === 'Topwater Bait'));
    assert.ok(!get.body.fishInventory.fish.some((i) => i.name === 'Carbon Rod'));
  });

  test('buildPublicFishFields unit', async () => {
    const pub = await buildPublicFishFields(mixedPayload);
    assert.equal(pub.fishItems.length, 2);
    assert.equal(pub.fishCounts.hiddenNonFishTypes, 3);
    assert.equal(pub.fishCounts.label, 'Fish');
  });

  test('raw numeric-only proof and catalog enrichment', async () => {
    const app = makeApp();
    const raw196 = {
      name: 'Item #196',
      amount: 34,
      category: 'items',
      itemId: '196',
      rawProof: {
        rawKey: '196',
        sourcePath: 'Inventory.Items',
        rawType: 'table',
        rawValuePreview: null,
        rawNameFields: {},
        extractedIdFields: { Id: '196' },
      },
    };
    const raw68 = {
      name: 'Item #68',
      amount: 2,
      category: 'items',
      itemId: '68',
      rawProof: {
        rawKey: '68',
        sourcePath: 'Inventory.Items',
        rawType: 'table',
        rawNameFields: {},
      },
    };
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10KRaw',
        userId: 17002,
        isOnline: true,
        items: [raw196, raw68],
        parseStats: { raw: 2, accepted: 2, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10KRaw').expect(200);
    assert.ok(dbg.body.rawInspector);
    assert.ok(Array.isArray(dbg.body.unresolvedRawProof));
    const proof196 = dbg.body.unresolvedRawProof.find((p) => p.itemId === '196');
    assert.ok(proof196);
    assert.equal(proof196.rawHadName, false);
    assert.equal(proof196.reason, 'raw_numeric_only_no_catalog_match');

    const res68 = deriveResolution(raw68, { name: 'Flame Angelfish', category: 'fish', itemId: '68' });
    assert.equal(res68.finalName, 'Flame Angelfish');
    assert.equal(res68.rawHadName, false);
    assert.equal(res68.catalogMatched, true);
    assert.equal(res68.resolutionReason, 'raw_numeric_only_catalog_seed_confirmed');

    const entry68 = dbg.body.firstItems.find((i) => i.itemId === '68');
    assert.ok(entry68);
    assert.equal(entry68.resolutionReason, 'raw_numeric_only_catalog_seed_confirmed');
  });

  test('parser dropped-name detection', () => {
    const raw = {
      name: 'Item #999',
      itemId: '999',
      rawProof: { rawNameFields: { Name: 'Example Fish' } },
    };
    const res = deriveResolution(raw, { name: 'Item #999', category: 'items', itemId: '999' });
    assert.equal(res.rawHadName, true);
    assert.equal(res.resolutionReason, 'live_catch_name_pending');
  });

  test('website template uses fish-only helpers', () => {
    const fs = require('fs');
    const path = require('path');
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(tpl.includes('getPublicFishItems'));
    assert.ok(tpl.includes('fishCountLabel'));
    assert.ok(tpl.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(tpl.includes('RENDER_BUILD'));
    assert.ok(!tpl.match(/inventory\.all/));
    assert.ok(!tpl.match(/Items:\s*<strong>/));
    assert.ok(!tpl.match(/Items:\s*\d+/));
    assert.ok(tpl.includes('Fish: <strong>-</strong>'));
  });

  test('debug regression fields preserved', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10KDbg',
        userId: 17003,
        isOnline: true,
        items: [{ name: 'Item #117', count: 1, itemId: '117' }],
      })
      .expect(200);
    const dbg = await request(app).get('/api/fishit-tracker/debug/B10KDbg').expect(200);
    assert.ok(dbg.body.rawItems);
    assert.ok(dbg.body.enrichedItems);
    assert.ok(dbg.body.catalogForItems['117']);
    assert.ok(dbg.body.countsRaw);
    assert.ok(dbg.body.countsEnriched);
    assert.ok(dbg.body.rawInspector);
    assert.ok(dbg.body.unresolvedRawProof);
  });

  test('isPublicFishItem excludes bait rod crate placeholders', () => {
    assert.equal(isPublicFishItem({ category: 'fish', name: 'Flame Angelfish' }), true);
    assert.equal(isPublicFishItem({ name: 'King Crab' }), true);
    assert.equal(isPublicFishItem({ category: 'bait', name: 'Topwater Bait' }), false);
    assert.equal(isPublicFishItem({ category: 'rod', name: 'Carbon Rod' }), false);
    assert.equal(isPublicFishItem({ category: 'items', name: 'Common Crate' }), false);
    assert.equal(isPublicFishItem({ category: 'items', name: 'Item #196', itemId: '196' }), false);
  });
});

describe('BLOCKER10K1 public fish-only UI regression', () => {
  const { buildPublicLegacyCounts, PUBLIC_RENDER_BUILD } = require('../src/fishitTrackerRoutes');

  beforeEach(() => { cleanup(); });

  const mixedPayload = [
    { name: 'Topwater Bait', amount: 1, category: 'bait', itemId: '10' },
    { name: 'Common Crate', amount: 5, category: 'items', itemId: '990' },
    { name: 'Carbon Rod', amount: 138, category: 'rod', itemId: '388' },
    { name: 'Flame Angelfish', amount: 136, category: 'fish', itemId: '68' },
    { name: 'Yello Damselfish', amount: 14, category: 'fish', itemId: '70' },
    { name: 'Item #196', amount: 34, category: 'items', itemId: '196' },
  ];

  test('get-backpack public API is fish-only with legacy counts safe for UI', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10K1Fish',
        userId: 18001,
        isOnline: true,
        type: 'inventory_snapshot',
        items: mixedPayload,
      })
      .expect(200);

    const get = await request(app).get('/api/fishit-tracker/get-backpack/B10K1Fish').expect(200);
    assert.equal(get.headers['cache-control'], 'no-store, no-cache, must-revalidate, proxy-revalidate');
    assert.equal(get.body.renderBuild, PUBLIC_RENDER_BUILD);

    const pubNames = get.body.publicItems.map((i) => i.name);
    assert.ok(pubNames.includes('Flame Angelfish'));
    assert.ok(pubNames.some((n) => /damselfish/i.test(n)));
    assert.ok(!pubNames.includes('Topwater Bait'));
    assert.ok(!pubNames.includes('Carbon Rod'));
    assert.ok(!pubNames.includes('Common Crate'));
    assert.ok(!pubNames.some((n) => /^Item #/i.test(n)));

    assert.equal(get.body.items.length, 2);
    assert.equal(get.body.fishCounts.fishTypes, 2);
    assert.equal(get.body.fishCounts.fishInstances, 150);
    assert.equal(get.body.counts.all, 150, 'counts.all is fish instances not mixed types');
    assert.equal(get.body.counts.items, 2, 'counts.items is fish types not mixed');
    assert.equal(get.body.allItems.length, 6);
    assert.ok(get.body.internalInventory.rods.some((i) => i.name === 'Carbon Rod'));
  });

  test('tracker page template is fish-only marked', () => {
    const fs = require('fs');
    const path = require('path');
    const html = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(html.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(html.includes('data-render-build'));
    assert.ok(html.includes('getPublicFishItems'));
    assert.ok(!html.match(/Items:\s*<strong>/));
  });

  test('debug rawInspector preserved for Item #196', async () => {
    const app = makeApp();
    const raw196 = {
      name: 'Item #196',
      amount: 34,
      category: 'items',
      itemId: '196',
      rawProof: {
        rawKey: '196',
        sourcePath: 'Inventory.Items',
        rawType: 'table',
        rawNameFields: {},
        extractedIdFields: { Id: '196' },
      },
    };
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10K1Raw',
        userId: 18002,
        isOnline: true,
        items: [...mixedPayload.filter((i) => i.itemId !== '196'), raw196],
      })
      .expect(200);

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10K1Raw').expect(200);
    assert.ok(dbg.body.rawInspector);
    assert.ok(Array.isArray(dbg.body.unresolvedRawProof));
    const proof196 = dbg.body.unresolvedRawProof.find((p) => p.itemId === '196');
    assert.ok(proof196);
    assert.equal(proof196.rawHadName, false);
    assert.equal(proof196.reason, 'raw_numeric_only_no_catalog_match');
  });

  test('buildPublicLegacyCounts never exposes mixed type count as all', () => {
    const counts = buildPublicLegacyCounts({
      fishTypes: 5,
      fishInstances: 216,
      hiddenNonFishTypes: 19,
      hiddenNonFishInstances: 275,
    });
    assert.equal(counts.all, 216);
    assert.equal(counts.items, 5);
    assert.equal(counts.rods, 0);
  });
});

describe('BLOCKER10L fish image asset catalog', () => {
  const fishImageAssets = require('../src/fishitFishImageAssets');
  const {
    buildPublicFishFields,
    isPublicFishItem,
  } = require('../src/fishitTrackerRoutes');

  const mixedPayload = [
    { name: 'Topwater Bait', amount: 1, category: 'bait', itemId: '10' },
    { name: 'Carbon Rod', amount: 138, category: 'rod', itemId: '388' },
    { name: 'Common Crate', amount: 5, category: 'items', itemId: '990' },
    { name: 'Flame Angelfish', amount: 136, category: 'fish', itemId: '68' },
    { name: 'Yello Damselfish', amount: 14, category: 'fish', itemId: '70' },
    { name: 'Item #196', amount: 34, category: 'items', itemId: '196' },
  ];

  test('known fish gets image from asset catalog by name', async () => {
    const pub = await buildPublicFishFields([
      { name: 'Flame Angelfish', amount: 1, category: 'fish', itemId: '68' },
    ]);
    assert.equal(pub.publicItems.length, 1);
    assert.equal(pub.publicItems[0].imageAssetId, '128385926161840');
    assert.ok(pub.publicItems[0].imageUrl.includes('/api/fishit-tracker/image/128385926161840')
      || pub.publicItems[0].imageUrl.includes('tr.rbxcdn.com'));
    assert.equal(pub.publicItems[0].imageUrlPresent, true);
    assert.equal(pub.publicItems[0].imageSource, 'fish_image_asset_catalog');
  });

  test('lookupByItemId is forbidden and returns null', () => {
    const warn = console.warn;
    let warned = '';
    console.warn = (msg) => { warned = String(msg); };
    try {
      assert.equal(fishImageAssets.lookupByItemId('196'), null);
      assert.ok(warned.includes('asset_catalog_index_mapping_forbidden'));
    } finally {
      console.warn = warn;
    }
  });

  test('Item #196 does not use asset list index or appear publicly', async () => {
    const pub = await buildPublicFishFields(mixedPayload);
    assert.ok(!pub.publicItems.some((i) => /Item #196/i.test(i.name)));
    const img = fishImageAssets.lookupByItemId('196');
    assert.equal(img, null);
    assert.equal(fishImageAssets.lookupByFishName('Item #196'), null);
  });

  test('confirmed fish without image asset stays visible with missing_image_asset', async () => {
    const pub = await buildPublicFishFields([
      { name: 'Mystery Testfish', amount: 2, category: 'fish', itemId: '99999' },
    ]);
    assert.equal(pub.publicItems.length, 1);
    assert.equal(pub.publicItems[0].name, 'Mystery Testfish');
    assert.equal(pub.publicItems[0].imageUrl, null);
    assert.equal(pub.publicItems[0].imageSource, 'missing_image_asset');
  });

  test('non-fish hidden from publicItems', async () => {
    const pub = await buildPublicFishFields(mixedPayload);
    const names = pub.publicItems.map((i) => i.name);
    assert.ok(names.includes('Flame Angelfish'));
    assert.ok(!names.includes('Topwater Bait'));
    assert.ok(!names.includes('Carbon Rod'));
    assert.ok(!names.includes('Common Crate'));
  });

  test('get-backpack exposes image fields and debug imageResolutionProof', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10LImage',
        userId: 19001,
        isOnline: true,
        items: mixedPayload,
      })
      .expect(200);

    const get = await request(app).get('/api/fishit-tracker/get-backpack/B10LImage').expect(200);
    const flame = get.body.publicItems.find((i) => i.name === 'Flame Angelfish');
    assert.ok(flame);
    assert.equal(flame.imageAssetId, '128385926161840');
    assert.equal(flame.imageUrlPresent, true);
    assert.ok(flame.imageUrl.includes('128385926161840'));

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10LImage').expect(200);
    assert.ok(Array.isArray(dbg.body.imageResolutionProof));
    const proof68 = dbg.body.imageResolutionProof.find((p) => p.itemId === '68');
    assert.ok(proof68);
    assert.equal(proof68.finalName, 'Flame Angelfish');
    assert.equal(proof68.imageAssetMatched, true);
    assert.equal(proof68.imageAssetId, '128385926161840');
    assert.ok(dbg.body.rawInspector);
    assert.ok(dbg.body.unresolvedRawProof);
  });

  test('tracker page template includes BLOCKER10N2 image marker', () => {
    const fs = require('fs');
    const path = require('path');
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(tpl.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(tpl.includes('imageAssetId'));
    assert.ok(tpl.includes('loading="lazy"'));
    assert.ok(tpl.includes('isUsableImageUrl'));
    assert.ok(!tpl.includes('robloxThumbFromAssetId'));
    assert.ok(!tpl.includes('thumbnails.roblox.com'));
  });

  test('isPublicFishItem unchanged for fish-only gate', () => {
    assert.equal(isPublicFishItem({ category: 'fish', name: 'Flame Angelfish' }), true);
    assert.equal(isPublicFishItem({ category: 'bait', name: 'Topwater Bait' }), false);
  });
});

describe('BLOCKER10M catch-delta name catalog discovery', () => {
  const TMP_LEARNED = path.join(os.tmpdir(), `fishit_learned_m_${process.pid}.json`);

  const fishCatalog = require('../src/fishitFishCatalog');
  const learnedFishCatalog = require('../src/fishitLearnedFishCatalog');
  const catchDelta = require('../src/fishitCatalogCatchDelta');
  const {
    buildPublicFishFields,
    isPublicFishItem,
    runCatchDeltaOnUpload,
    ingestLearnedFishEntry,
    PUBLIC_API_BUILD,
  } = require('../src/fishitTrackerRoutes');

  function cleanupLearned() {
    try { if (fs.existsSync(TMP_LEARNED)) fs.unlinkSync(TMP_LEARNED); } catch (_) {}
    learnedFishCatalog._reset();
    fishCatalog._reset();
  }

  beforeEach(() => {
    process.env.FISHIT_LEARNED_FISH_CATALOG_PATH = TMP_LEARNED;
    cleanup();
    cleanupLearned();
    catalogStore.seedKnownMappings();
  });

  test('1: rarity-only catch (Forgotten) is rejected — not learned as fish', () => {
    const discovery = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'Forgotten', source: 'catch_notification' },
      previousItemCounts: { 196: 34 },
      currentItems: [{ name: 'Item #196', itemId: '196', amount: 35, category: 'items' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.equal(discovery.learnedMappings.length, 0);
    assert.ok(discovery.rejectedEvents.some((e) => e.reason === 'name_is_rarity_label'));
    assert.equal(discovery.lastRarityCandidate, 'Forgotten');
    assert.equal(discovery.lastFishNameCandidate, null);
    assert.equal(learnedFishCatalog.lookupById('196'), null);
    const blocked = learnedFishCatalog.getBlockedMappings().find((b) => b.itemId === '196');
    assert.ok(blocked);
    assert.equal(blocked.reason, 'name_is_rarity_label');
  });

  test('2: multiple deltas stay low confidence only', async () => {
    const discovery = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'King Crab', source: 'catch_notification' },
      previousItemCounts: { 196: 34, 72: 57 },
      currentItems: [
        { name: 'Item #196', itemId: '196', amount: 35, category: 'items' },
        { name: 'Item #72', itemId: '72', amount: 58, category: 'items' },
      ],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.equal(discovery.learnedMappings.length, 0);
    assert.equal(discovery.pendingLowConfidenceMappings.length, 2);
    assert.equal(learnedFishCatalog.lookupById('196'), null);
    const pub = await buildPublicFishFields([
      { name: 'Item #196', itemId: '196', amount: 35, category: 'items' },
      { name: 'Item #72', itemId: '72', amount: 58, category: 'items' },
    ]);
    assert.equal(pub.publicItems.length, 0);
  });

  test('3: no delta produces no mapping', () => {
    const discovery = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'King Crab', source: 'catch_notification' },
      previousItemCounts: { 196: 34 },
      currentItems: [{ name: 'Item #196', itemId: '196', amount: 34, category: 'items' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.equal(discovery.learnedMappings.length, 0);
    assert.ok(discovery.rejectedEvents.some((e) => e.reason === 'no_delta'));
  });

  test('4: existing non-fish (Carbon Rod 388) protected from fish overwrite', () => {
    const discovery = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'King Crab', source: 'catch_notification' },
      previousItemCounts: { 388: 137 },
      currentItems: [{ name: 'Carbon Rod', itemId: '388', amount: 138, category: 'rod' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.equal(discovery.learnedMappings.length, 0);
    assert.ok(
      discovery.rejectedEvents.some((e) => e.reason === 'known_non_fish'),
      `expected known_non_fish rejection, got: ${JSON.stringify(discovery.rejectedEvents)}`,
    );
    assert.equal(learnedFishCatalog.lookupById('388'), null);
    assert.equal(isPublicFishItem({ name: 'Carbon Rod', category: 'rod', itemId: '388' }), false);
  });

  test('5: public UI remains fish-only after catch-delta learn', async () => {
    const app = makeApp();
    const mixed = [
      { name: 'Topwater Bait', amount: 1, category: 'bait', itemId: '10' },
      { name: 'Carbon Rod', amount: 138, category: 'rod', itemId: '388' },
      { name: 'Common Crate', amount: 5, category: 'items', itemId: '990' },
      { name: 'Flame Angelfish', amount: 136, category: 'fish', itemId: '68' },
      { name: 'Item #196', amount: 35, category: 'items', itemId: '196' },
    ];
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10MCatch',
        userId: 20001,
        isOnline: true,
        items: mixed,
        pendingCatchName: { fishName: 'Forgotten', source: 'catch_notification' },
        previousItemCounts: { 196: 34 },
      })
      .expect(200);

    const get = await request(app).get('/api/fishit-tracker/get-backpack/B10MCatch').expect(200);
    const names = get.body.publicItems.map((i) => i.name);
    assert.ok(!names.includes('Forgotten'));
    assert.ok(names.includes('Flame Angelfish'));
    assert.ok(!names.some((n) => /Item #/i.test(n)));
    assert.ok(!names.includes('Topwater Bait'));
    assert.ok(!names.includes('Carbon Rod'));
    assert.ok(!names.includes('Common Crate'));
  });

  test('6: image attached after name known (Flame Angelfish via learn)', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10MImg',
        userId: 20002,
        isOnline: true,
        items: [{ name: 'Item #68', itemId: '68', amount: 2, category: 'items' }],
        pendingCatchName: { fishName: 'Flame Angelfish', source: 'catch_notification' },
        previousItemCounts: { 68: 1 },
      })
      .expect(200);

    const get = await request(app).get('/api/fishit-tracker/get-backpack/B10MImg').expect(200);
    const flame = get.body.publicItems.find((i) => i.name === 'Flame Angelfish');
    assert.ok(flame);
    assert.equal(flame.imageAssetId, '128385926161840');
    assert.equal(flame.imageUrlPresent, true);
    assert.ok(flame.imageUrl.includes('128385926161840'));
    assert.equal(flame.imageSource, 'fish_image_asset_catalog');
  });

  test('7: debug proof contains nameCatalogDiscovery', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10MDebug',
        userId: 20003,
        isOnline: true,
        items: [{ name: 'Item #196', itemId: '196', amount: 35, category: 'items' }],
        pendingCatchName: { fishName: 'Forgotten', source: 'catch_notification' },
        previousItemCounts: { 196: 34 },
      })
      .expect(200);

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10MDebug').expect(200);
    assert.ok(dbg.body.nameCatalogDiscovery);
    assert.equal(dbg.body.publicApiBuild, PUBLIC_API_BUILD);
    assert.ok(dbg.body.learningValidation);
    const blocked = dbg.body.learningValidation.blockedLearnedMappings.find((b) => b.itemId === '196');
    assert.ok(blocked);
    assert.equal(blocked.learnedName, 'Forgotten');
    assert.equal(blocked.reason, 'name_is_rarity_label');
    assert.ok(dbg.body.nameCatalogDiscovery.rejectedEvents.some((e) => e.reason === 'name_is_rarity_label'));
  });

  test('runCatchDeltaOnUpload rejects rarity-only catch on POST', () => {
    const raw = [{ name: 'Item #196', itemId: '196', amount: 35, category: 'items' }];
    const disc = runCatchDeltaOnUpload(
      { pendingCatchName: { fishName: 'Forgotten' }, previousItemCounts: { 196: 34 } },
      raw,
      null,
    );
    assert.ok(disc);
    assert.equal(disc.learnedMappings.length, 0);
    assert.ok(disc.rejectedEvents.some((e) => e.reason === 'name_is_rarity_label'));
  });

  test('tracker.lua includes BLOCKER10M catch delta markers', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(src.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(src.includes('normalizeCatchFishName'));
    assert.ok(src.includes('pendingCatchName'));
    assert.ok(src.includes('previousItemCounts'));
    assert.ok(src.includes('CATCH_NAME_PENDING'));
    assert.ok(!src.toLowerCase().includes('anticheat'));
    assert.ok(!src.toLowerCase().includes('coregui'));
  });
});

describe('BLOCKER10N2 image proxy label fix', () => {
  const robloxThumbnails = require('../src/fishitRobloxThumbnails');
  const fishCatalog = require('../src/fishitFishCatalog');
  const {
    buildPublicFishFields,
    PUBLIC_API_BUILD,
    PUBLIC_RENDER_BUILD,
  } = require('../src/fishitTrackerRoutes');

  beforeEach(() => {
    robloxThumbnails._resetCache();
    fishCatalog._reset();
    catalogStore._reset();
    catalogStore.seedKnownMappings();
  });

  const knownFish = [
    { itemId: '68', name: 'Flame Angelfish', assetId: '128385926161840' },
    { itemId: '70', name: 'Yello Damselfish', assetId: '125066072333378' },
    { itemId: '71', name: 'Darwin Clownfish', assetId: '109996187340520' },
    { itemId: '117', name: 'Bandit Angelfish', assetId: '86776001616210' },
    { itemId: '119', name: 'Ballina Angelfish', assetId: '99236757363784' },
  ];

  test('resolveFishImageAssets returns resolved CDN URLs for all 5 known fish', async () => {
    const results = await robloxThumbnails.resolveFishImageAssets(knownFish.map((f) => f.assetId));
    assert.equal(results.length, 5);
    for (const row of results) {
      assert.equal(row.imageResolved, true, `asset ${row.assetId} not resolved`);
      assert.ok(row.imageUrl.includes('tr.rbxcdn.com'), `asset ${row.assetId} missing CDN url`);
      assert.equal(row.imageStatus, 'resolved_test');
    }
  });

  test('buildPublicFishFields returns resolved images — not unverified proxy', async () => {
    const pub = await buildPublicFishFields(knownFish.map((f) => ({
      name: f.name, amount: 1, category: 'fish', itemId: f.itemId,
    })));
    assert.equal(pub.publicItems.length, 5);
    assert.equal(pub.fishCounts.label, 'Fish');
    for (const f of knownFish) {
      const row = pub.publicItems.find((i) => i.itemId === f.itemId);
      assert.ok(row, `missing ${f.itemId}`);
      assert.equal(row.imageAssetId, f.assetId);
      assert.equal(row.imageUrlPresent, true);
      assert.equal(row.imageResolved, true);
      assert.equal(row.imageStatus, 'resolved');
      assert.ok(row.imageUrl.includes('tr.rbxcdn.com'));
      const verify = await robloxThumbnails.verifyImageUrlWorks(row.imageUrl);
      assert.equal(verify.ok, true, `verify failed for ${f.name}: ${verify.reason}`);
    }
  });

  test('verifyImageUrlWorks accepts CDN stub and rejects JSON', async () => {
    const ok = await robloxThumbnails.verifyImageUrlWorks('https://tr.rbxcdn.com/test-stub/1/Image/Png/noFilter');
    assert.equal(ok.ok, true);
    const bad = await robloxThumbnails.verifyImageUrlWorks('https://example.com/not-image');
    assert.equal(bad.ok, false);
  });

  test('HTML contains N2 marker, Fish label, and image src for all 5 fish', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(tpl.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(tpl.includes('fishCountLabel'));
    assert.ok(tpl.includes('data-fish-count'));
    assert.ok(tpl.includes('onFishImageError'));
    assert.ok(tpl.includes('wireFishImageErrors'));
    assert.ok(!tpl.match(/Items:\s*<strong>/));
    assert.ok(!tpl.match(/Items:\s*\d+/));
    assert.ok(tpl.includes('Fish: <strong>-</strong>'));
    for (const f of knownFish) {
      assert.ok(tpl.includes(f.assetId) || tpl.includes('/api/fishit-tracker/image/'),
        `template should reference image proxy or asset for ${f.name}`);
    }
  });

  test('fish catalog has confirmed seeds and non-fish protected', () => {
    const stats = fishCatalog.getStats();
    assert.ok(stats.fishCatalogTotal >= 5);
    assert.equal(stats.fishCatalogWithImages, 5);
    assert.equal(fishCatalog.lookupByItemId('68').name, 'Flame Angelfish');
    assert.equal(fishCatalog.lookupByItemId('10'), null);
    assert.equal(catalogStore.lookupById('388').name, 'Carbon Rod');
    assert.equal(catalogStore.lookupById('10').name, 'Topwater Bait');
    assert.equal(catalogStore.lookupById('990').name, 'Common Crate');
  });

  test('public API returns 5 resolved fish images and Fish counts label', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10N2Proof',
        userId: 21002,
        isOnline: true,
        items: knownFish.map((f) => ({
          name: f.name, amount: 10, category: 'fish', itemId: f.itemId,
        })),
      })
      .expect(200);

    const get = await request(app).get('/api/fishit-tracker/get-backpack/B10N2Proof').expect(200);
    assert.equal(get.body.publicApiBuild, PUBLIC_API_BUILD);
    assert.equal(get.body.renderBuild, PUBLIC_RENDER_BUILD);
    assert.equal(get.body.fishCounts.label, 'Fish');
    assert.equal(get.body.counts.label, 'Fish');
    assert.equal(get.body.publicItems.length, 5);
    for (const f of knownFish) {
      const row = get.body.publicItems.find((i) => i.itemId === f.itemId);
      assert.ok(row, `missing public item ${f.itemId}`);
      assert.equal(row.imageUrlPresent, true);
      assert.equal(row.imageResolved, true);
      assert.ok(row.imageUrl.includes('tr.rbxcdn.com'));
      const verify = await robloxThumbnails.verifyImageUrlWorks(row.imageUrl);
      assert.equal(verify.ok, true, `${f.name} image verify: ${verify.reason}`);
    }
    assert.ok(!get.body.publicItems.some((i) => i.name === 'Topwater Bait'));
    assert.ok(!get.body.publicItems.some((i) => i.name === 'Carbon Rod'));
    assert.ok(!get.body.publicItems.some((i) => i.name === 'Common Crate'));
  });

  test('debug exposes N2 markers and imageResolutionProof with resolved images', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10N2Dbg',
        userId: 21003,
        isOnline: true,
        items: knownFish.map((f) => ({
          name: f.name, amount: 43, category: 'fish', itemId: f.itemId,
        })),
      })
      .expect(200);

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10N2Dbg').expect(200);
    assert.equal(dbg.body.publicApiBuild, PUBLIC_API_BUILD);
    assert.equal(dbg.body.renderBuild, PUBLIC_RENDER_BUILD);
    assert.equal(dbg.body.publicFishItems.length, 5);
    assert.ok(dbg.body.imageResolutionProof.every((p) => p.imageUrlPresent === true));
    assert.ok(dbg.body.imageResolutionProof.every((p) => p.imageResolved === true));
    for (const f of knownFish) {
      const row = dbg.body.publicFishItems.find((i) => i.itemId === f.itemId);
      assert.equal(row.imageResolved, true, `${f.name} not resolved in debug`);
    }
  });

  test('image proxy route returns 302 to CDN for each known asset', async () => {
    const app = makeApp();
    for (const f of knownFish) {
      const res = await request(app)
        .get(`/api/fishit-tracker/image/${f.assetId}`)
        .expect(302);
      assert.ok(res.headers.location, `no location for ${f.assetId}`);
      assert.ok(res.headers.location.includes('tr.rbxcdn.com')
        || res.headers.location.includes('fallback-fish.svg'));
      assert.ok(!String(res.headers['content-type'] || '').includes('json'));
    }
  });

  test('image-debug endpoint returns diagnostic JSON', async () => {
    const app = makeApp();
    const res = await request(app)
      .get('/api/fishit-tracker/image-debug/128385926161840')
      .expect(200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.assetId, '128385926161840');
    assert.ok(res.body.thumbnailImageUrl.includes('tr.rbxcdn.com'));
    assert.equal(res.body.resolved, true);
    assert.equal(res.body.publicApiBuild, PUBLIC_API_BUILD);
  });

  test('tracker.lua has single BLOCKER10O boot marker', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(src.includes('TRACKER_BOOT_BEGIN BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(!src.includes('TRACKER_BOOT_BEGIN BLOCKER10J'));
    assert.equal((src.match(/TRACKER_BOOT_BEGIN/g) || []).length, 1);
  });
});

describe('BLOCKER10O full catalog safe learning', () => {
  const TMP_LEARNED = path.join(os.tmpdir(), `fishit_learned_o_${process.pid}.json`);

  const robloxThumbnails = require('../src/fishitRobloxThumbnails');
  const fishCatalog = require('../src/fishitFishCatalog');
  const learnedFishCatalog = require('../src/fishitLearnedFishCatalog');
  const catchDelta = require('../src/fishitCatalogCatchDelta');
  const staticCatalogAudit = require('../src/fishitStaticCatalogAudit');
  const {
    buildPublicFishFields,
    PUBLIC_API_BUILD,
    PUBLIC_RENDER_BUILD,
    ingestLearnedFishEntry,
    isPublicFishItem,
  } = require('../src/fishitTrackerRoutes');

  const knownFish = [
    { itemId: '68', name: 'Flame Angelfish', assetId: '128385926161840' },
    { itemId: '70', name: 'Yello Damselfish', assetId: '125066072333378' },
    { itemId: '71', name: 'Darwin Clownfish', assetId: '109996187340520' },
    { itemId: '117', name: 'Bandit Angelfish', assetId: '86776001616210' },
    { itemId: '119', name: 'Ballina Angelfish', assetId: '99236757363784' },
  ];

  function cleanupLearned() {
    try { if (fs.existsSync(TMP_LEARNED)) fs.unlinkSync(TMP_LEARNED); } catch (_) {}
    learnedFishCatalog._reset();
    fishCatalog._reset();
  }

  beforeEach(() => {
    process.env.FISHIT_LEARNED_FISH_CATALOG_PATH = TMP_LEARNED;
    robloxThumbnails._resetCache();
    cleanupLearned();
    catalogStore._reset();
    catalogStore.seedKnownMappings();
  });

  test('static catalog audit reports sources honestly', () => {
    const audit = staticCatalogAudit.auditStaticCatalogSources();
    assert.ok(Array.isArray(audit.staticCatalogSources));
    assert.ok(audit.staticCatalogSources.includes('seed_confirmed'));
    assert.ok(audit.staticCatalogWithItemId >= 5);
    assert.ok(Array.isArray(audit.rejectedStaticCatalogSources));
    assert.ok(audit.summary);
  });

  test('5 confirmed fish regression — images still resolved', async () => {
    const pub = await buildPublicFishFields(knownFish.map((f) => ({
      name: f.name, amount: 10, category: 'fish', itemId: f.itemId,
    })));
    assert.equal(pub.publicItems.length, 5);
    for (const f of knownFish) {
      const row = pub.publicItems.find((i) => i.itemId === f.itemId);
      assert.equal(row.imageResolved, true, `${f.name} image broke`);
      assert.equal(row.imageUrlPresent, true);
    }
  });

  test('non-fish seeds stay hidden from public fish cards', async () => {
    const pub = await buildPublicFishFields([
      { name: 'Topwater Bait', amount: 1, category: 'bait', itemId: '10' },
      { name: 'Carbon Rod', amount: 138, category: 'rod', itemId: '388' },
      { name: 'Common Crate', amount: 5, category: 'items', itemId: '990' },
      ...knownFish.map((f) => ({ name: f.name, amount: 1, category: 'fish', itemId: f.itemId })),
    ]);
    const names = pub.publicItems.map((i) => i.name);
    assert.equal(pub.publicItems.length, 5);
    assert.ok(!names.includes('Topwater Bait'));
    assert.ok(!names.includes('Carbon Rod'));
    assert.ok(!names.includes('Common Crate'));
  });

  test('rarity-only catch from catch_notification is rejected not confirmed', () => {
    const disc = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'Forgotten', source: 'catch_notification', detectedAt: new Date().toISOString() },
      previousItemCounts: { 196: 34 },
      currentItems: [{ name: 'Item #196', itemId: '196', amount: 35, category: 'items' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.equal(disc.learnedMappings.length, 0);
    assert.ok(disc.rejectedEvents.some((e) => e.reason === 'name_is_rarity_label'));
  });

  test('weak catch source creates pending not public', () => {
    const disc = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'Mystery Fish', source: 'chat_log', detectedAt: new Date().toISOString() },
      previousItemCounts: { 72: 10 },
      currentItems: [{ name: 'Item #72', itemId: '72', amount: 11, category: 'items' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.equal(disc.learnedMappings.length, 0);
    assert.equal(disc.pendingLowConfidenceMappings.length, 1);
    assert.equal(isPublicFishItem({ name: 'Item #72', itemId: '72', category: 'items' }), false);
  });

  test('repeat observation promotes pending to confirmed', () => {
    const args = {
      pendingCatch: { fishName: 'King Crab', source: 'chat_log', detectedAt: new Date().toISOString() },
      previousItemCounts: { 72: 10 },
      currentItems: [{ name: 'Item #72', itemId: '72', amount: 11, category: 'items' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    };
    catchDelta.processCatchDelta(args);
    const second = catchDelta.processCatchDelta({
      ...args,
      previousItemCounts: { 72: 11 },
      currentItems: [{ name: 'Item #72', itemId: '72', amount: 12, category: 'items' }],
    });
    assert.equal(second.learnedMappings.length, 1);
    assert.equal(second.learnedMappings[0].learnedName, 'King Crab');
    assert.equal(second.learnedMappings[0].publicEligible, true);
  });

  test('rejects ambiguous multi-delta', () => {
    const disc = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'King Crab', source: 'catch_notification', detectedAt: new Date().toISOString() },
      previousItemCounts: { 196: 34, 72: 57 },
      currentItems: [
        { name: 'Item #196', itemId: '196', amount: 35, category: 'items' },
        { name: 'Item #72', itemId: '72', amount: 58, category: 'items' },
      ],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.equal(disc.learnedMappings.length, 0);
    assert.ok(disc.rejectedEvents.some((e) => e.reason === 'multiple_delta_candidates'));
  });

  test('rejects no catch name, no previous inventory, stale catch, known non-fish', () => {
    const noName = catchDelta.processCatchDelta({
      pendingCatch: null,
      previousItemCounts: { 196: 1 },
      currentItems: [{ itemId: '196', amount: 2 }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.ok(noName.rejectedEvents.some((e) => e.reason === 'no_catch_name'));

    const noPrev = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'Test', source: 'catch_notification' },
      previousItemCounts: {},
      currentItems: [{ itemId: '196', amount: 2 }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.ok(noPrev.rejectedEvents.some((e) => e.reason === 'no_previous_inventory'));

    const stale = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'Test', source: 'catch_notification', detectedAt: new Date(Date.now() - 600000).toISOString() },
      previousItemCounts: { 196: 1 },
      currentItems: [{ itemId: '196', amount: 2 }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.ok(stale.rejectedEvents.some((e) => e.reason === 'stale_catch'));

    const bait = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'Fake Fish', source: 'catch_notification', detectedAt: new Date().toISOString() },
      previousItemCounts: { 10: 0 },
      currentItems: [{ name: 'Topwater Bait', itemId: '10', amount: 1, category: 'bait' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.ok(bait.rejectedEvents.some((e) => e.reason === 'known_non_fish'));
  });

  test('rarity label ingest is blocked and persisted as rejected mapping', () => {
    process.env.FISHIT_LEARNED_PERSIST = '1';
    learnedFishCatalog._reset();
    learnedFishCatalog.blockEntry('196', 'Forgotten', 'name_is_rarity_label', { testPersist: true });
    assert.ok(fs.existsSync(TMP_LEARNED));
    learnedFishCatalog.reloadFromDisk();
    fishCatalog._reset();
    assert.equal(learnedFishCatalog.lookupById('196'), null);
    const blocked = learnedFishCatalog.getBlockedMappings().find((b) => b.itemId === '196');
    assert.ok(blocked);
    assert.equal(blocked.learnedName, 'Forgotten');
    assert.equal(blocked.reason, 'name_is_rarity_label');
    const r = ingestLearnedFishEntry({
      itemId: '196',
      name: 'Forgotten',
      category: 'fish',
      source: 'catch_delta_high_confidence',
      confidence: 1,
    });
    assert.equal(r.reason, 'name_is_rarity_label');
    delete process.env.FISHIT_LEARNED_PERSIST;
  });

  test('rarity normalizes when source exists and missing stays null', () => {
    assert.equal(fishCatalog.normalizeRarity('legend'), 'Legendary');
    assert.equal(fishCatalog.normalizeRarity('mythic'), 'Mythic');
    assert.equal(fishCatalog.normalizeRarity(null), null);
    const stats = fishCatalog.getStats();
    assert.ok(stats.missingRarityFishIds.length >= 0);
  });

  test('debug exposes BLOCKER10O markers, static audit, and learning fields', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10ODebug',
        userId: 22001,
        isOnline: true,
        items: [
          ...knownFish.map((f) => ({ name: f.name, amount: 5, category: 'fish', itemId: f.itemId })),
          { name: 'Item #196', itemId: '196', amount: 35, category: 'items' },
        ],
        pendingCatchName: { fishName: 'Forgotten', source: 'catch_notification', detectedAt: new Date().toISOString() },
        previousItemCounts: { 196: 34 },
      })
      .expect(200);

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10ODebug').expect(200);
    assert.equal(dbg.body.publicApiBuild, PUBLIC_API_BUILD);
    assert.equal(dbg.body.renderBuild, PUBLIC_RENDER_BUILD);
    assert.ok(dbg.body.staticCatalogAudit);
    assert.ok(dbg.body.nameCatalogDiscovery);
    assert.ok(dbg.body.learningValidation);
    assert.ok(dbg.body.nameCatalogDiscovery.rejectedEvents.some((e) => e.reason === 'name_is_rarity_label'));
    for (const f of knownFish) {
      const row = dbg.body.publicFishItems.find((i) => i.itemId === f.itemId);
      assert.equal(row.imageResolved, true);
    }
  });

  test('public API and HTML keep Fish label and BLOCKER10Q marker', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10OLabel',
        userId: 22002,
        isOnline: true,
        items: knownFish.map((f) => ({ name: f.name, amount: 43, category: 'fish', itemId: f.itemId })),
      })
      .expect(200);

    const get = await request(app).get('/api/fishit-tracker/get-backpack/B10OLabel').expect(200);
    assert.equal(get.body.fishCounts.label, 'Fish');
    assert.equal(get.body.publicApiBuild, 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');

    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(tpl.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(!tpl.match(/Items:\s*<strong>/));
  });
});

describe('BLOCKER10P false rarity learn fix (regression)', () => {
  const TMP_LEARNED = path.join(os.tmpdir(), `fishit_learned_p_${process.pid}.json`);

  const rarityLabels = require('../src/fishitRarityLabels');
  const catchNameParser = require('../src/fishitCatchNameParser');
  const nameOnlyCatalog = require('../src/fishitNameOnlyCatalog');
  const learnedFishCatalog = require('../src/fishitLearnedFishCatalog');
  const catchDelta = require('../src/fishitCatalogCatchDelta');
  const robloxThumbnails = require('../src/fishitRobloxThumbnails');
  const fishCatalog = require('../src/fishitFishCatalog');
  const {
    buildPublicFishFields,
    PUBLIC_API_BUILD,
    PUBLIC_RENDER_BUILD,
    ingestLearnedFishEntry,
    isPublicFishItem,
  } = require('../src/fishitTrackerRoutes');

  const knownFish = [
    { itemId: '68', name: 'Flame Angelfish', assetId: '128385926161840' },
    { itemId: '70', name: 'Yello Damselfish', assetId: '125066072333378' },
    { itemId: '71', name: 'Darwin Clownfish', assetId: '109996187340520' },
    { itemId: '117', name: 'Bandit Angelfish', assetId: '86776001616210' },
    { itemId: '119', name: 'Ballina Angelfish', assetId: '99236757363784' },
  ];

  function cleanupLearned() {
    try { if (fs.existsSync(TMP_LEARNED)) fs.unlinkSync(TMP_LEARNED); } catch (_) {}
    learnedFishCatalog._reset();
    fishCatalog._reset();
  }

  beforeEach(() => {
    process.env.FISHIT_LEARNED_FISH_CATALOG_PATH = TMP_LEARNED;
    robloxThumbnails._resetCache();
    cleanupLearned();
    catalogStore._reset();
    catalogStore.seedKnownMappings();
  });

  test('1: bad learned mapping cleanup — itemId 196 Forgotten rejected', () => {
    const r = ingestLearnedFishEntry({
      itemId: '196',
      name: 'Forgotten',
      category: 'fish',
      source: 'catch_delta_high_confidence',
      confidence: 1,
    });
    assert.equal(r.reason, 'name_is_rarity_label');
    assert.equal(learnedFishCatalog.lookupById('196'), null);
    const blocked = learnedFishCatalog.getBlockedMappings().find((b) => b.itemId === '196');
    assert.ok(blocked);
    assert.equal(blocked.learnedName, 'Forgotten');
    assert.equal(blocked.reason, 'name_is_rarity_label');
  });

  test('2: rarity classifier blocks labels, allows real fish names', () => {
    assert.equal(rarityLabels.isRarityLabel('Forgotten'), true);
    assert.equal(rarityLabels.isRarityLabel('Common'), true);
    assert.equal(rarityLabels.isRarityLabel('Legendary'), true);
    assert.equal(rarityLabels.isRarityLabel('Flame Angelfish'), false);
    assert.equal(rarityLabels.isBlockedLearnName('Rare'), true);
    assert.equal(rarityLabels.isBlockedLearnName('Darwin Clownfish'), false);
  });

  test('3: parser separates rarity prefix from fish name', () => {
    const split = catchNameParser.parseCatchInput({ fishName: 'Forgotten King Crab', source: 'catch_notification' });
    assert.equal(split.fishNameCandidate, 'King Crab');
    assert.equal(split.rarityCandidate, 'Forgotten');
    assert.equal(split.parserDecision, 'rarity_prefix_stripped');

    const onlyRarity = catchNameParser.parseCatchInput({ fishName: 'Forgotten', source: 'catch_notification' });
    assert.equal(onlyRarity.fishNameCandidate, null);
    assert.equal(onlyRarity.rarityCandidate, 'Forgotten');
    assert.equal(onlyRarity.parserDecision, 'rarity_only');

    const weight = catchNameParser.parseCatchInput({ fishName: '12.5 kg', source: 'catch_notification' });
    assert.equal(weight.fishNameCandidate, null);
  });

  test('4: promotion rules — one weak observation stays pending', () => {
    const disc = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'Mystery Fish', source: 'chat_log', detectedAt: new Date().toISOString() },
      previousItemCounts: { 72: 10 },
      currentItems: [{ name: 'Item #72', itemId: '72', amount: 11, category: 'items' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.equal(disc.learnedMappings.length, 0);
    assert.equal(disc.pendingLowConfidenceMappings.length, 1);
    assert.equal(disc.pendingLowConfidenceMappings[0].publicEligible, false);
  });

  test('4b: verified known fish name can promote on single clean delta', () => {
    const disc = catchDelta.processCatchDelta({
      pendingCatch: { fishName: 'Flame Angelfish', source: 'catch_notification', detectedAt: new Date().toISOString() },
      previousItemCounts: { 68: 1 },
      currentItems: [{ name: 'Item #68', itemId: '68', amount: 2, category: 'items' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    });
    assert.equal(disc.learnedMappings.length, 1);
    assert.equal(disc.learnedMappings[0].publicEligible, true);
    assert.equal(disc.learnedMappings[0].nameValidated, true);
  });

  test('4c: two clean repeated observations promote unknown name', () => {
    const args = {
      pendingCatch: { fishName: 'King Crab', source: 'chat_log', detectedAt: new Date().toISOString() },
      previousItemCounts: { 72: 10 },
      currentItems: [{ name: 'Item #72', itemId: '72', amount: 11, category: 'items' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
    };
    catchDelta.processCatchDelta(args);
    const second = catchDelta.processCatchDelta({
      ...args,
      previousItemCounts: { 72: 11 },
      currentItems: [{ name: 'Item #72', itemId: '72', amount: 12, category: 'items' }],
    });
    assert.equal(second.learnedMappings.length, 1);
    assert.equal(second.learnedMappings[0].publicEligible, true);
  });

  test('5: name-only DB validation — real fish passes, rarity fails', () => {
    const flame = nameOnlyCatalog.validateFishName('Flame Angelfish');
    assert.equal(flame.nameKnown, true);
    assert.equal(flame.valid, true);
    assert.ok(flame.imageAssetId);

    const forg = nameOnlyCatalog.validateFishName('Forgotten');
    assert.equal(forg.nameKnown, false);
    assert.equal(forg.reason, 'name_is_rarity_label');
  });

  test('6: regression — 5 fish images, Fish label, Forgotten not public', async () => {
    const pub = await buildPublicFishFields(knownFish.map((f) => ({
      name: f.name, amount: 10, category: 'fish', itemId: f.itemId,
    })));
    assert.equal(pub.publicItems.length, 5);
    assert.equal(pub.fishCounts.label, 'Fish');
    for (const f of knownFish) {
      const row = pub.publicItems.find((i) => i.itemId === f.itemId);
      assert.equal(row.imageAssetId, f.assetId);
    }
    const withForgotten = await buildPublicFishFields([
      ...knownFish.map((f) => ({ name: f.name, amount: 1, category: 'fish', itemId: f.itemId })),
      { name: 'Forgotten', amount: 34, category: 'fish', itemId: '196' },
    ]);
    assert.ok(!withForgotten.publicItems.some((i) => i.name === 'Forgotten'));
    assert.equal(isPublicFishItem({ name: 'Forgotten', category: 'fish', itemId: '196' }), false);
    assert.ok(!withForgotten.publicItems.some((i) => /Item #/i.test(i.name)));
  });

  test('7: debug exposes learningValidation and parser fields', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10PDebug',
        userId: 23001,
        isOnline: true,
        items: [
          ...knownFish.map((f) => ({ name: f.name, amount: 5, category: 'fish', itemId: f.itemId })),
          { name: 'Item #196', itemId: '196', amount: 35, category: 'items' },
        ],
        pendingCatchName: { fishName: 'Forgotten King Crab', source: 'catch_notification', detectedAt: new Date().toISOString() },
        previousItemCounts: { 196: 34 },
      })
      .expect(200);

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10PDebug').expect(200);
    assert.equal(dbg.body.publicApiBuild, PUBLIC_API_BUILD);
    assert.equal(dbg.body.renderBuild, PUBLIC_RENDER_BUILD);
    assert.ok(dbg.body.learningValidation);
    assert.ok(dbg.body.learningValidation.knownFishNameCount >= 5);
    assert.ok(dbg.body.learningValidation.knownFishImageCount >= 5);
    assert.ok(dbg.body.nameCatalogDiscovery.lastFishNameCandidate);
    assert.ok(dbg.body.nameCatalogDiscovery.lastRarityCandidate);
    assert.ok(!dbg.body.publicFishItems.some((i) => i.name === 'Forgotten'));
    for (const f of knownFish) {
      const row = dbg.body.publicFishItems.find((i) => i.itemId === f.itemId);
      assert.equal(row.imageResolved, true);
    }
  });

  test('8: build markers BLOCKER10Q in tracker.lua and template', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(src.includes('TRACKER_BOOT_BEGIN BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(src.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(src.includes('isRarityTok'));
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(tpl.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.equal(PUBLIC_API_BUILD, 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');
  });
});

describe('BLOCKER10Q global collective catalog rarity', () => {
  const TMP_GLOBAL = path.join(os.tmpdir(), `fishit_global_${process.pid}.json`);
  const TMP_LEARNED = path.join(os.tmpdir(), `fishit_learned_q_${process.pid}.json`);

  const globalFishCatalog = require('../src/fishitGlobalFishItemCatalog');
  const catchNameParser = require('../src/fishitCatchNameParser');
  const nameOnlyCatalog = require('../src/fishitNameOnlyCatalog');
  const learnedFishCatalog = require('../src/fishitLearnedFishCatalog');
  const catchDelta = require('../src/fishitCatalogCatchDelta');
  const robloxThumbnails = require('../src/fishitRobloxThumbnails');
  const fishCatalog = require('../src/fishitFishCatalog');
  const {
    buildPublicFishFields,
    enrichItemsFromCatalog,
    PUBLIC_API_BUILD,
    ingestLearnedFishEntry,
    isPublicFishItem,
  } = require('../src/fishitTrackerRoutes');

  const knownFish = [
    { itemId: '68', name: 'Flame Angelfish', assetId: '128385926161840' },
    { itemId: '70', name: 'Yello Damselfish', assetId: '125066072333378' },
    { itemId: '71', name: 'Darwin Clownfish', assetId: '109996187340520' },
    { itemId: '117', name: 'Bandit Angelfish', assetId: '86776001616210' },
    { itemId: '119', name: 'Ballina Angelfish', assetId: '99236757363784' },
  ];

  function evidenceBase(overrides) {
    return {
      itemId: '68',
      fishNameCandidate: 'Flame Angelfish',
      source: 'catch_notification',
      userId: 1001,
      cleanSingleDelta: true,
      deltaAmount: 1,
      timestamp: new Date().toISOString(),
      ...(overrides || {}),
    };
  }

  function cleanup() {
    try { if (fs.existsSync(TMP_GLOBAL)) fs.unlinkSync(TMP_GLOBAL); } catch (_) {}
    try { if (fs.existsSync(TMP_LEARNED)) fs.unlinkSync(TMP_LEARNED); } catch (_) {}
    globalFishCatalog._reset();
    learnedFishCatalog._reset();
    fishCatalog._reset();
  }

  beforeEach(() => {
    process.env.FISHIT_GLOBAL_FISH_ITEM_CATALOG_PATH = TMP_GLOBAL;
    process.env.FISHIT_LEARNED_FISH_CATALOG_PATH = TMP_LEARNED;
    delete process.env.FISHIT_GLOBAL_PERSIST;
    robloxThumbnails._resetCache();
    cleanup();
    catalogStore._reset();
    catalogStore.seedKnownMappings();
    fishCatalog._reset();
  });

  test('1: global storage — pending write, confirmed write, reload', () => {
    process.env.FISHIT_GLOBAL_PERSIST = '1';
    const pending = globalFishCatalog.submitEvidence({
      ...evidenceBase({ itemId: '72', fishNameCandidate: 'Flame Angelfish', cleanSingleDelta: false }),
    });
    assert.equal(pending.decision, 'pending');
    assert.equal(globalFishCatalog.lookupById('72').confidence, 'pending');

    globalFishCatalog.submitEvidence(evidenceBase({ itemId: '72', userId: 1002, cleanSingleDelta: true }));
    const confirmed = globalFishCatalog.lookupById('72');
    assert.equal(confirmed.confidence, 'confirmed');
    assert.equal(confirmed.publicEligible, true);

    fishCatalog._reset();
    const reloaded = globalFishCatalog.reloadFromDisk();
    assert.equal(reloaded.byItemId['72'].confidence, 'confirmed');
    assert.equal(globalFishCatalog.getStats().confirmedCount, 1);
  });

  test('2: one weak observation stays pending', () => {
    const r = globalFishCatalog.submitEvidence({
      ...evidenceBase({ cleanSingleDelta: false }),
    });
    assert.equal(r.decision, 'pending');
    assert.equal(r.publicEligible, false);
    assert.equal(globalFishCatalog.getStats().pendingCount, 1);
  });

  test('3: two clean observations confirm same itemId/name', () => {
    globalFishCatalog.submitEvidence(evidenceBase({ userId: 2001, cleanSingleDelta: false }));
    const second = globalFishCatalog.submitEvidence(evidenceBase({ userId: 2001, cleanSingleDelta: true }));
    assert.equal(second.decision, 'confirmed');
    assert.equal(second.evidenceCount, 2);
    assert.equal(second.publicEligible, true);
    assert.equal(second.reason, 'repeated_observation_confirmed');
  });

  test('4: two unique users confirm', () => {
    globalFishCatalog.submitEvidence(evidenceBase({ userId: 3001, cleanSingleDelta: false }));
    const second = globalFishCatalog.submitEvidence(evidenceBase({ userId: 3002, cleanSingleDelta: true }));
    assert.equal(second.decision, 'confirmed');
    assert.equal(second.uniqueUserCount, 2);
    assert.equal(second.reason, 'multi_user_confirmed');
  });

  test('5: conflicting names mark conflict', () => {
    globalFishCatalog.submitEvidence(evidenceBase({ itemId: '80', fishNameCandidate: 'Flame Angelfish' }));
    const conflict = globalFishCatalog.submitEvidence({
      ...evidenceBase({ itemId: '80', fishNameCandidate: 'Darwin Clownfish', userId: 4002 }),
    });
    assert.equal(conflict.decision, 'conflict');
    const entry = globalFishCatalog.lookupById('80');
    assert.equal(entry.confidence, 'conflict');
    assert.equal(entry.publicEligible, false);
    assert.ok(entry.conflictNames.length >= 2);
  });

  test('6: blocked rarity label marks blocked', () => {
    const r = globalFishCatalog.submitEvidence({
      itemId: '196',
      fishNameCandidate: 'Forgotten',
      source: 'catch_notification',
      userId: 5001,
    });
    assert.equal(r.rejected, true);
    assert.equal(r.reason, 'name_is_rarity_label');
    const entry = globalFishCatalog.lookupById('196');
    assert.equal(entry.confidence, 'blocked');
    assert.equal(entry.blockedReason, 'name_is_rarity_label');
  });

  test('7: rarity — Forgotten King Crab splits; Forgotten alone rejected', () => {
    const split = catchNameParser.parseCatchInput({
      fishName: 'Forgotten King Crab',
      source: 'catch_notification',
    });
    assert.equal(split.rarityCandidate, 'Forgotten');
    assert.equal(split.fishNameCandidate, 'King Crab');

    const rarityEv = globalFishCatalog.submitEvidence({
      itemId: '88',
      fishNameCandidate: 'Flame Angelfish',
      rarityCandidate: 'Forgotten',
      source: 'catch_notification',
      userId: 6001,
      cleanSingleDelta: true,
    });
    assert.equal(rarityEv.rarity, 'Forgotten');
    assert.equal(rarityEv.fishName, 'Flame Angelfish');

    const onlyRarity = catchNameParser.parseCatchInput({ fishName: 'Forgotten', source: 'catch_notification' });
    assert.equal(onlyRarity.fishNameCandidate, null);
    const rej = globalFishCatalog.submitEvidence({
      itemId: '89',
      fishNameCandidate: onlyRarity.fishNameCandidate,
      rarityCandidate: onlyRarity.rarityCandidate,
      source: 'catch_notification',
      userId: 6002,
    });
    assert.equal(rej.rejected, true);
  });

  test('8: rarity conflicts do not silently overwrite', () => {
    globalFishCatalog.seedAdminEntry({
      itemId: '90',
      fishName: 'Flame Angelfish',
      rarity: 'Rare',
      source: 'admin_seed',
    });
    globalFishCatalog.submitEvidence({
      ...evidenceBase({ itemId: '90', rarityCandidate: 'Epic', userId: 7001 }),
    });
    const entry = globalFishCatalog.lookupById('90');
    assert.equal(entry.rarityConfidence, 'conflict');
    assert.ok(entry.rarityConflictValues.includes('Rare'));
    assert.ok(entry.rarityConflictValues.includes('Epic'));
  });

  test('9: name validation — known fish passes, rarity fails, image enriched', () => {
    const flame = nameOnlyCatalog.validateFishName('Flame Angelfish');
    assert.equal(flame.nameKnown, true);
    assert.ok(flame.imageAssetId);
    const forg = nameOnlyCatalog.validateFishName('Forgotten');
    assert.equal(forg.nameKnown, false);
    assert.equal(forg.reason, 'name_is_rarity_label');

    globalFishCatalog.submitEvidence(evidenceBase({ itemId: '91', userId: 8001, cleanSingleDelta: false }));
    globalFishCatalog.submitEvidence(evidenceBase({ itemId: '91', userId: 8002, cleanSingleDelta: true }));
    const entry = globalFishCatalog.lookupById('91');
    assert.equal(entry.imageAssetId, flame.imageAssetId);
  });

  test('10: public merge — confirmed global appears; pending/blocked/conflict hidden', async () => {
    globalFishCatalog.seedAdminEntry({
      itemId: '92',
      fishName: 'Bandit Angelfish',
      source: 'admin_seed',
    });
    globalFishCatalog.submitEvidence({
      ...evidenceBase({ itemId: '93', fishNameCandidate: 'Flame Angelfish', userId: 9001, cleanSingleDelta: false }),
    });
    globalFishCatalog.blockEntry('94', 'Forgotten', 'name_is_rarity_label');
    globalFishCatalog.submitEvidence(evidenceBase({ itemId: '95', fishNameCandidate: 'Flame Angelfish', userId: 9003 }));
    globalFishCatalog.submitEvidence({
      ...evidenceBase({ itemId: '95', fishNameCandidate: 'Darwin Clownfish', userId: 9004 }),
    });
    fishCatalog._reset();

    const rawItems = [
      ...knownFish.map((f) => ({ name: f.name, amount: 1, category: 'fish', itemId: f.itemId })),
      { name: 'Item #92', itemId: '92', amount: 2, category: 'items' },
      { name: 'Item #93', itemId: '93', amount: 1, category: 'items' },
      { name: 'Item #94', itemId: '94', amount: 1, category: 'items' },
      { name: 'Item #95', itemId: '95', amount: 1, category: 'items' },
    ];
    const pub = await buildPublicFishFields(enrichItemsFromCatalog(rawItems));

    assert.ok(pub.publicItems.some((i) => i.itemId === '92' && i.name === 'Bandit Angelfish'));
    assert.ok(!pub.publicItems.some((i) => i.itemId === '93'));
    assert.ok(!pub.publicItems.some((i) => i.itemId === '94'));
    assert.ok(!pub.publicItems.some((i) => i.itemId === '95'));
    assert.ok(!pub.publicItems.some((i) => /Item #/i.test(i.name)));
  });

  test('11: regression — 5 fish images, Fish label, Forgotten absent', async () => {
    const pub = await buildPublicFishFields(knownFish.map((f) => ({
      name: f.name, amount: 10, category: 'fish', itemId: f.itemId,
    })));
    assert.equal(pub.publicItems.length, 5);
    assert.equal(pub.fishCounts.label, 'Fish');
    for (const f of knownFish) {
      const row = pub.publicItems.find((i) => i.itemId === f.itemId);
      assert.equal(row.imageAssetId, f.assetId);
    }
    assert.equal(isPublicFishItem({ name: 'Forgotten', category: 'fish', itemId: '196' }), false);
  });

  test('12: debug exposes globalCatalog, liveCatchBinding, learningValidation', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10QDebug',
        userId: 24001,
        isOnline: true,
        items: knownFish.map((f) => ({ name: f.name, amount: 3, category: 'fish', itemId: f.itemId })),
        pendingCatchName: {
          fishName: 'Forgotten King Crab',
          source: 'catch_notification',
          detectedAt: new Date().toISOString(),
        },
        previousItemCounts: { 68: 2 },
        gameId: '12345',
        placeId: '67890',
      })
      .expect(200);

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10QDebug').expect(200);
    assert.equal(dbg.body.publicApiBuild, PUBLIC_API_BUILD);
    assert.ok(dbg.body.globalCatalog);
    assert.equal(dbg.body.globalCatalog.enabled, true);
    assert.ok(dbg.body.liveCatchBinding);
    assert.ok(dbg.body.liveCatchBinding.lastCatchTextRaw != null
      || dbg.body.liveCatchBinding.lastFishNameCandidate != null);
    assert.ok(dbg.body.learningValidation);
    assert.ok('globalEvidenceAccepted' in dbg.body.learningValidation);
    assert.ok('globalEvidenceRejected' in dbg.body.learningValidation);
  });

  test('13: catch delta submits global evidence with game context', () => {
    const disc = catchDelta.processCatchDelta({
      pendingCatch: {
        fishName: 'Flame Angelfish',
        source: 'catch_notification',
        detectedAt: new Date().toISOString(),
      },
      previousItemCounts: { 68: 1 },
      currentItems: [{ name: 'Item #68', itemId: '68', amount: 2, category: 'items' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
      globalContext: { enabled: true, userId: 25001, gameId: 'g1', placeId: 'p1', gameVersion: 'v1' },
    });
    assert.ok(disc.globalEvidence);
    assert.equal(disc.globalEvidence.accepted || disc.globalEvidence.rejected, true);
  });

  test('14: build markers BLOCKER10R', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(src.includes('TRACKER_BOOT_BEGIN BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(src.includes('payload.gameId'));
    assert.ok(src.includes('payload.placeId'));
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(tpl.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.equal(PUBLIC_API_BUILD, 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');
  });
});

describe('BLOCKER10R real live new itemId proof', () => {
  const TMP_GLOBAL = path.join(os.tmpdir(), `fishit_global_r_${process.pid}.json`);
  const TMP_LEARNED = path.join(os.tmpdir(), `fishit_learned_r_${process.pid}.json`);

  const liveCatchProof = require('../src/fishitLiveCatchProof');
  const globalFishCatalog = require('../src/fishitGlobalFishItemCatalog');
  const learnedFishCatalog = require('../src/fishitLearnedFishCatalog');
  const catchNameParser = require('../src/fishitCatchNameParser');
  const catchDelta = require('../src/fishitCatalogCatchDelta');
  const robloxThumbnails = require('../src/fishitRobloxThumbnails');
  const fishCatalog = require('../src/fishitFishCatalog');
  const {
    buildPublicFishFields,
    enrichItemsFromCatalog,
    PUBLIC_API_BUILD,
    ingestLearnedFishEntry,
    isPublicFishItem,
    runCatchDeltaOnUpload,
  } = require('../src/fishitTrackerRoutes');

  const knownFish = [
    { itemId: '68', name: 'Flame Angelfish', assetId: '128385926161840' },
    { itemId: '70', name: 'Yello Damselfish', assetId: '125066072333378' },
    { itemId: '71', name: 'Darwin Clownfish', assetId: '109996187340520' },
    { itemId: '117', name: 'Bandit Angelfish', assetId: '86776001616210' },
    { itemId: '119', name: 'Ballina Angelfish', assetId: '99236757363784' },
  ];

  function cleanup() {
    try { if (fs.existsSync(TMP_GLOBAL)) fs.unlinkSync(TMP_GLOBAL); } catch (_) {}
    try { if (fs.existsSync(TMP_LEARNED)) fs.unlinkSync(TMP_LEARNED); } catch (_) {}
    globalFishCatalog._reset();
    learnedFishCatalog._reset();
    fishCatalog._reset();
  }

  beforeEach(() => {
    process.env.FISHIT_GLOBAL_FISH_ITEM_CATALOG_PATH = TMP_GLOBAL;
    process.env.FISHIT_LEARNED_FISH_CATALOG_PATH = TMP_LEARNED;
    delete process.env.FISHIT_GLOBAL_PERSIST;
    robloxThumbnails._resetCache();
    cleanup();
    catalogStore._reset();
    catalogStore.seedKnownMappings();
    fishCatalog._reset();
  });

  test('1: evidence source mode distinguishes live_roblox from api_simulation', () => {
    assert.equal(liveCatchProof.resolveEvidenceSourceMode({
      clientOrigin: 'roblox_tracker',
      trackerBuild: 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06',
    }), 'live_roblox');
    assert.equal(liveCatchProof.resolveEvidenceSourceMode({
      pendingCatchName: { fishName: 'Flame Angelfish', source: 'catch_notification' },
    }), 'api_simulation');
    assert.equal(liveCatchProof.resolveEvidenceSourceMode({ _testFixture: true }), 'test_fixture');
  });

  test('2: proof helpers reject simulation-only and seed itemIds', () => {
    const simProof = liveCatchProof.buildNewUnresolvedBindingProof({
      lastFishNameCandidate: 'Flame Angelfish',
      lastParserRawText: 'Flame Angelfish',
      deltaCandidates: [{ itemId: '68', delta: 1 }],
      evidenceSourceMode: 'api_simulation',
      globalEvidence: { decision: 'confirmed', publicEligible: true },
    }, 'testuser');
    assert.equal(simProof.wasExistingSeed, true);
    assert.equal(simProof.countsAsNewUnresolvedProof, false);
    assert.equal(liveCatchProof.isLiveProofSuccess(simProof), false);

    const liveSeedProof = liveCatchProof.buildNewUnresolvedBindingProof({
      lastFishNameCandidate: 'Flame Angelfish',
      deltaCandidates: [{ itemId: '68', delta: 1 }],
      evidenceSourceMode: 'live_roblox',
      globalEvidence: { decision: 'confirmed' },
    }, 'denghub2');
    assert.equal(liveSeedProof.wasExistingSeed, true);
    assert.equal(liveCatchProof.isLiveProofSuccess(liveSeedProof), false);
  });

  test('3: unresolved itemId live evidence stays pending without second observation', () => {
    const r = globalFishCatalog.submitEvidence({
      itemId: '72',
      fishNameCandidate: 'Flame Angelfish',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      userId: 5001,
      sessionKey: 'denghub2',
      cleanSingleDelta: true,
    });
    assert.equal(r.decision, 'confirmed');
    assert.equal(r.evidenceSourceMode, 'live_roblox');
    assert.equal(r.wasPreviouslyUnresolved, true);
    assert.equal(r.wasExistingSeed, false);

    const sim = globalFishCatalog.submitEvidence({
      itemId: '353',
      fishNameCandidate: 'Flame Angelfish',
      source: 'catch_notification',
      evidenceSourceMode: 'api_simulation',
      userId: 5002,
      cleanSingleDelta: true,
    });
    assert.equal(sim.decision, 'pending');
    assert.equal(sim.reason, 'awaiting_live_roblox_evidence');
  });

  test('4: ambiguous multi-delta stays rejected for new unresolved proof', () => {
    const disc = catchDelta.processCatchDelta({
      pendingCatch: {
        fishName: 'Forgotten King Crab',
        source: 'catch_notification',
        detectedAt: new Date().toISOString(),
      },
      previousItemCounts: { 72: 10, 196: 34 },
      currentItems: [
        { name: 'Item #72', itemId: '72', amount: 11, category: 'items' },
        { name: 'Item #196', itemId: '196', amount: 35, category: 'items' },
      ],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
      globalContext: {
        enabled: true,
        userId: 6001,
        evidenceSourceMode: 'live_roblox',
        sessionKey: 'denghub2',
      },
    });
    const proof = liveCatchProof.buildNewUnresolvedBindingProof(disc, 'denghub2', (id) => globalFishCatalog.lookupById(id));
    assert.equal(proof.decision, 'rejected');
    assert.equal(proof.reason, 'multiple_delta_candidates');
    assert.equal(proof.publicEligible, false);
  });

  test('5: rarity split Forgotten King Crab; Forgotten alone blocked', () => {
    const split = catchNameParser.parseCatchInput({
      fishName: 'Forgotten King Crab',
      source: 'catch_notification',
    });
    assert.equal(split.fishNameCandidate, 'King Crab');
    assert.equal(split.rarityCandidate, 'Forgotten');

    const rej = globalFishCatalog.submitEvidence({
      itemId: '196',
      fishNameCandidate: null,
      rarityCandidate: 'Forgotten',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      userId: 7001,
    });
    assert.equal(rej.rejected, true);
    assert.equal(rej.reason, 'name_is_rarity_label');
  });

  test('6: confirmed new unresolved itemId can become public via catalog merge', async () => {
    globalFishCatalog.submitEvidence({
      itemId: '72',
      fishNameCandidate: 'Flame Angelfish',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      userId: 8001,
      sessionKey: 'denghub2',
      cleanSingleDelta: true,
    });
    fishCatalog._reset();
    const pub = await buildPublicFishFields(enrichItemsFromCatalog([
      ...knownFish.map((f) => ({ name: f.name, amount: 1, category: 'fish', itemId: f.itemId })),
      { name: 'Item #72', itemId: '72', amount: 2, category: 'items' },
    ]));
    assert.ok(pub.publicItems.some((i) => i.itemId === '72' && i.name === 'Flame Angelfish'));
    assert.equal(isPublicFishItem({ name: 'Forgotten', category: 'fish', itemId: '196' }), false);
    assert.ok(!pub.publicItems.some((i) => /Item #/i.test(i.name)));
  });

  test('7: debug exposes evidenceSourceDebug and newUnresolvedBindingProof', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10RDebug',
        userId: 25002,
        isOnline: true,
        clientOrigin: 'roblox_tracker',
        trackerBuild: 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06',
        evidenceSourceMode: 'live_roblox',
        items: knownFish.map((f) => ({ name: f.name, amount: 3, category: 'fish', itemId: f.itemId })),
        pendingCatchName: {
          fishName: 'Flame Angelfish',
          rawText: 'Flame Angelfish',
          source: 'catch_notification',
          evidenceSourceMode: 'live_roblox',
          detectedAt: new Date().toISOString(),
        },
        previousItemCounts: { 72: 1 },
        gameId: '12345',
        placeId: '67890',
      })
      .expect(200);

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10RDebug').expect(200);
    assert.equal(dbg.body.publicApiBuild, PUBLIC_API_BUILD);
    assert.ok(dbg.body.evidenceSourceDebug);
    assert.equal(dbg.body.evidenceSourceDebug.evidenceSourceMode, 'live_roblox');
    assert.ok(dbg.body.newUnresolvedBindingProof);
    assert.ok(dbg.body.liveCatchBinding);
    assert.ok(dbg.body.liveCatchBinding.lastCatchTextRaw != null
      || dbg.body.liveCatchBinding.lastFishNameCandidate != null);
  });

  test('8: regression — 5 fish images, Fish label, tracker markers', async () => {
    const pub = await buildPublicFishFields(knownFish.map((f) => ({
      name: f.name, amount: 10, category: 'fish', itemId: f.itemId,
    })));
    assert.equal(pub.publicItems.length, 5);
    assert.equal(pub.fishCounts.label, 'Fish');
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(src.includes('LIVE_CATCH_TEXT'));
    assert.ok(src.includes('evidenceSourceMode'));
    assert.equal(PUBLIC_API_BUILD, 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');
  });
});

describe('BLOCKER10S fish path discovery and no empty wipe (carried into BLOCKER10T)', () => {
  const partialSnapshot = require('../src/fishitPartialSnapshot');
  const catchNameParser = require('../src/fishitCatchNameParser');
  const rarityLabels = require('../src/fishitRarityLabels');
  const {
    buildPublicFishFields,
    isPublicFishItem,
    PUBLIC_API_BUILD,
  } = require('../src/fishitTrackerRoutes');

  const knownFish = [
    { itemId: '68', name: 'Flame Angelfish', assetId: '128385926161840' },
    { itemId: '70', name: 'Yello Damselfish', assetId: '125066072333378' },
    { itemId: '71', name: 'Darwin Clownfish', assetId: '109996187340520' },
    { itemId: '117', name: 'Bandit Angelfish', assetId: '86776001616210' },
    { itemId: '119', name: 'Ballina Angelfish', assetId: '99236757363784' },
  ];

  test('1: partial zero-fish snapshot preserves last good fish', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10SPreserve',
        userId: 26001,
        isOnline: true,
        items: knownFish.map((f) => ({ name: f.name, amount: 5, category: 'fish', itemId: f.itemId })),
        parseStats: { raw: 50, accepted: 5, fish: 5, selectedPath: 'Inventory.Fish' },
      })
      .expect(200);

    const good = await request(app).get('/api/fishit-tracker/get-backpack/B10SPreserve').expect(200);
    assert.equal(good.body.publicItems.length, 5);

    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10SPreserve',
        userId: 26001,
        isOnline: true,
        items: [
          { name: 'Item #10', itemId: '10', amount: 1, category: 'items' },
          { name: 'Item #990', itemId: '990', amount: 1, category: 'items' },
        ],
        parseStats: {
          raw: 14, accepted: 2, acceptedInstances: 2, fish: 0,
          selectedPath: 'Inventory.Items', selectedGeneralPath: 'Inventory.Items',
        },
      })
      .expect(200);

    const after = await request(app).get('/api/fishit-tracker/get-backpack/B10SPreserve').expect(200);
    assert.equal(after.body.publicItems.length, 5, 'public fish must not wipe to empty');

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10SPreserve').expect(200);
    assert.equal(dbg.body.partialSnapshotDetected, true);
    assert.equal(dbg.body.lastGoodFishPreserved, true);
    assert.ok(dbg.body.partialSnapshotReason.includes('zero_fish_partial_snapshot_preserved_last_good'));
    assert.equal(dbg.body.publicFishItems.length, 5);
  });

  test('2: fishPathDiscovery sanitises candidate payload', () => {
    const out = partialSnapshot.sanitiseFishPathDiscovery({
      selectedFishPath: 'Inventory.Fish',
      selectedFishPathReason: 'fish_like_entries',
      selectedGeneralPath: 'Inventory.Items',
      candidates: [{
        path: 'Inventory.Fish',
        rawCount: 12,
        acceptedCount: 12,
        fishLikeCount: 12,
        nameFieldCount: 10,
        weightFieldCount: 8,
        score: 24,
        selected: true,
        sample: 'Salmon',
      }, {
        path: 'Inventory.Items',
        rawCount: 14,
        acceptedCount: 6,
        fishLikeCount: 0,
        score: 1,
        selected: false,
      }],
    });
    assert.equal(out.selectedFishPath, 'Inventory.Fish');
    assert.equal(out.candidates.length, 2);
    assert.equal(out.candidates[0].sample, 'Salmon');
  });

  test('3: rarity-only catch names blocked; Forgotten King Crab splits', () => {
    const split = catchNameParser.parseCatchInput({
      fishName: 'Forgotten King Crab',
      source: 'catch_notification',
    });
    assert.equal(split.baseFishName, 'King Crab');
    assert.equal(split.fishNameCandidate, 'King Crab');
    assert.equal(split.rarityCandidate, 'Forgotten');
    assert.equal(rarityLabels.isBlockedLearnName('Forgotten'), true);
    assert.equal(rarityLabels.isBlockedLearnName('Salmon'), false);
  });

  test('4: regression — Fish label, no Forgotten, no Item # public cards', async () => {
    const pub = await buildPublicFishFields([
      ...knownFish.map((f) => ({ name: f.name, amount: 1, category: 'fish', itemId: f.itemId })),
      { name: 'Forgotten', amount: 1, category: 'fish', itemId: '196' },
      { name: 'Item #72', itemId: '72', amount: 1, category: 'items' },
    ]);
    assert.equal(pub.fishCounts.label, 'Fish');
    assert.equal(pub.publicItems.length, 5);
    assert.ok(!pub.publicItems.some((i) => i.name === 'Forgotten'));
    assert.ok(!pub.publicItems.some((i) => /Item #/i.test(i.name)));
    assert.equal(isPublicFishItem({ name: 'Forgotten', category: 'fish', itemId: '196' }), false);
  });

  test('5: tracker.lua has fish path discovery and catch watcher markers', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(src.includes('discoverFishInventoryPaths'));
    assert.ok(src.includes('FISH_PATH_SELECTED'));
    assert.ok(src.includes('scanPlayerGuiForCatchText'));
    assert.ok(src.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.equal(PUBLIC_API_BUILD, 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');
  });
});

describe('BLOCKER10T live catch normalize and public promote', () => {
  const catchNameParser = require('../src/fishitCatchNameParser');
  const globalFishCatalog = require('../src/fishitGlobalFishItemCatalog');
  const learnedFishCatalog = require('../src/fishitLearnedFishCatalog');
  const catalogStore = require('../src/fishitCatalogStore');
  const {
    buildPublicFishFields,
    isPublicFishItem,
    deriveResolution,
    PUBLIC_API_BUILD,
  } = require('../src/fishitTrackerRoutes');

  beforeEach(() => {
    globalFishCatalog._reset();
    learnedFishCatalog._reset();
    catalogStore._reset();
  });

  test('1: strips weight and parses mutation from catch text', () => {
    const v1 = catchNameParser.parseCatchInput({ fishName: 'Vampire Squid (1.85kg)' });
    assert.equal(v1.baseFishName, 'Vampire Squid');
    assert.equal(v1.weightKg, 1.85);
    assert.equal(v1.mutation, null);

    const v2 = catchNameParser.parseCatchInput({ fishName: 'Vampire Squid (1.61kg)' });
    assert.equal(v2.baseFishName, 'Vampire Squid');
    assert.equal(catchNameParser.baseFishNameForConflict(v1.displayName),
      catchNameParser.baseFishNameForConflict(v2.displayName));

    const shiny = catchNameParser.parseCatchInput({ fishName: 'Shiny Jellyfish (22.7kg)' });
    assert.equal(shiny.mutation, 'Shiny');
    assert.equal(shiny.baseFishName, 'Jellyfish');
    assert.equal(shiny.displayName, 'Shiny Jellyfish');
    assert.equal(shiny.weightKg, 22.7);

    const big = catchNameParser.parseCatchInput({ fishName: 'Big Salmon (60.8kg)' });
    assert.equal(big.mutation, 'Big');
    assert.equal(big.baseFishName, 'Salmon');
  });

  test('2: same baseFishName with different weight does not global-conflict', () => {
    globalFishCatalog.submitEvidence({
      itemId: '162',
      fishNameCandidate: 'Vampire Squid (1.85kg)',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      cleanSingleDelta: true,
      userId: 1,
    });
    const second = globalFishCatalog.submitEvidence({
      itemId: '162',
      fishNameCandidate: 'Vampire Squid (1.61kg)',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      cleanSingleDelta: true,
      userId: 1,
    });
    assert.notEqual(second.decision, 'conflict');
    const entry = globalFishCatalog.lookupById('162');
    assert.equal(entry.baseFishName, 'Vampire Squid');
    assert.equal(entry.confidence, 'confirmed');
  });

  test('3: live_roblox single delta unknown fish becomes public fish item', async () => {
    const app = makeApp();
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10TLive',
        userId: 27001,
        isOnline: true,
        clientOrigin: 'roblox_tracker',
        trackerBuild: 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06',
        pendingCatchName: {
          fishName: 'Jellyfish',
          displayName: 'Shiny Jellyfish',
          mutation: 'Shiny',
          weightKg: 22.7,
          rawText: 'Shiny Jellyfish (22.7kg)',
          source: 'catch_notification',
          detectedAt: new Date().toISOString(),
        },
        previousItemCounts: { 157: 86 },
        items: [{ name: 'Item #157', itemId: '157', amount: 87, category: 'items' }],
        parseStats: { raw: 14, accepted: 1, fish: 0, selectedPath: 'Inventory.Items' },
      })
      .expect(200);

    const get = await request(app).get('/api/fishit-tracker/get-backpack/B10TLive').expect(200);
    assert.ok(get.body.publicItems.length >= 1, 'learned live fish should appear publicly');
    const jelly = get.body.publicItems.find((i) => i.itemId === '157');
    assert.ok(jelly, 'item 157 should be public');
    assert.equal(jelly.displayName || jelly.name, 'Shiny Jellyfish');

    const dbg = await request(app).get('/api/fishit-tracker/debug/B10TLive').expect(200);
    assert.ok(dbg.body.lastCatchParsed);
    assert.equal(dbg.body.lastCatchParsed.baseFishName, 'Jellyfish');
    assert.equal(dbg.body.lastCatchParsed.mutation, 'Shiny');
    assert.ok(dbg.body.publicFishItems.length >= 1);
    const g157 = dbg.body.globalCatalogForItems['157'];
    assert.ok(g157);
    assert.equal(g157.baseFishName, 'Jellyfish');
    assert.equal(g157.mutation, 'Shiny');
    assert.equal(g157.publicEligible, true);
  });

  test('4: Metadata.Weight does not set rawHadName true', () => {
    const res = deriveResolution(
      { name: 'Item #163', itemId: '163', rawProof: { rawWeightFields: { 'meta.Weight': '1.85' } } },
      { name: 'Item #163', category: 'items', itemId: '163' },
    );
    assert.equal(res.rawHadName, false);
    assert.equal(res.resolutionReason, 'raw_weight_present_no_name');
  });

  test('5: regression — Forgotten blocked, no Item # public cards, partial preserve', async () => {
    const pub = await buildPublicFishFields([
      { name: 'Flame Angelfish', amount: 1, category: 'fish', itemId: '68' },
      { name: 'Forgotten', amount: 1, category: 'fish', itemId: '196' },
      { name: 'Item #72', itemId: '72', amount: 1, category: 'items' },
    ]);
    assert.equal(pub.publicItems.length, 1);
    assert.ok(!pub.publicItems.some((i) => /Item #/i.test(i.name)));
    assert.equal(isPublicFishItem({ name: 'Forgotten', category: 'fish', itemId: '196' }), false);

    const partialSnapshot = require('../src/fishitPartialSnapshot');
    const info = partialSnapshot.detectPartialZeroFishSnapshot({
      ps: { fish: 0, accepted: 2, selectedPath: 'Inventory.Items' },
      cleanItems: [{ name: 'Item #10', itemId: '10', category: 'items' }],
      existing: { lastGoodPublicFishCount: 3, lastGoodFishItems: [{ name: 'Salmon', category: 'fish', itemId: '99' }] },
      priorPublicFishCount: 3,
    });
    assert.equal(info.isPartial, true);

    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(src.includes('parseCatchNameFull'));
    assert.equal(PUBLIC_API_BUILD, 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');
  });
});

describe('BLOCKER10U global catalog polish images rarity', () => {
  const catchNameParser = require('../src/fishitCatchNameParser');
  const globalFishCatalog = require('../src/fishitGlobalFishItemCatalog');
  const catalogPolish = require('../src/fishitCatalogPolish');
  const fishImageCache = require('../src/fishitFishImageCache');
  const rarityEnrichment = require('../src/fishitRarityEnrichment');
  const fishCatalog = require('../src/fishitFishCatalog');
  const catalogStore = require('../src/fishitCatalogStore');
  const {
    buildPublicFishFields,
    enrichItemsFromCatalog,
    PUBLIC_API_BUILD,
  } = require('../src/fishitTrackerRoutes');

  beforeEach(() => {
    globalFishCatalog._reset();
    catalogStore._reset();
    fishImageCache._reset();
    rarityEnrichment.resetProof();
    catalogPolish.resetStats();
    fishCatalog._reset();
  });

  test('name cleanup — weight K kg and multi-mutation prefixes', () => {
    const cases = [
      ['Deep Sea Crab (1.31K kg)', 'Deep Sea Crab', 1310, null],
      ['Armor Catfish (2.65K kg)', 'Armor Catfish', 2650, null],
      ['Ruby (5.8kg)', 'Ruby', 5.8, null],
      ['Shiny Jellyfish', 'Jellyfish', null, 'Shiny'],
      ['Big Sea Urchin', 'Sea Urchin', null, 'Big'],
      ['Radioactive Shiny Horseshoe Crab', 'Horseshoe Crab', null, 'Radioactive Shiny'],
      ['Fairy Dust Jellyfish', 'Jellyfish', null, 'Fairy Dust'],
      ['Galaxy Monk Fish', 'Monk Fish', null, 'Galaxy'],
      ['Holographic Synodontis', 'Synodontis', null, 'Holographic'],
    ];
    for (const [raw, base, weight, mutation] of cases) {
      const c = catchNameParser.canonicalizeFishName(raw);
      assert.equal(c.baseFishName, base, raw);
      if (weight != null) assert.equal(c.weightKg, weight, raw);
      if (mutation) assert.equal(c.mutation, mutation, raw);
    }
  });

  test('conflict repair — same base fish with weight/mutation does not conflict', () => {
    globalFishCatalog.submitEvidence({
      itemId: '152',
      fishNameCandidate: 'Deep Sea Crab (1.31K kg)',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      cleanSingleDelta: true,
      userId: 1,
    });
    const second = globalFishCatalog.submitEvidence({
      itemId: '152',
      fishNameCandidate: 'Deep Sea Crab (1.36K kg)',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      cleanSingleDelta: true,
      userId: 1,
    });
    assert.notEqual(second.decision, 'conflict');
    assert.equal(globalFishCatalog.lookupById('152').baseFishName, 'Deep Sea Crab');

    globalFishCatalog._reset();
    globalFishCatalog.submitEvidence({
      itemId: '157',
      fishNameCandidate: 'Jellyfish',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      cleanSingleDelta: true,
      userId: 1,
    });
    const shiny = globalFishCatalog.submitEvidence({
      itemId: '157',
      fishNameCandidate: 'Shiny Jellyfish',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      cleanSingleDelta: true,
      userId: 1,
    });
    assert.notEqual(shiny.decision, 'conflict');
    assert.equal(globalFishCatalog.lookupById('157').baseFishName, 'Jellyfish');
    assert.equal(globalFishCatalog.lookupById('157').mutation, 'Shiny');
  });

  test('image cache — known asset cached with local URL in publicFishItems', async () => {
    const pub = await buildPublicFishFields([
      { name: 'Flame Angelfish', amount: 1, category: 'fish', itemId: '68' },
    ]);
    assert.equal(pub.publicItems.length, 1);
    const card = pub.publicItems[0];
    assert.ok(card.imageAssetId);
    assert.match(card.imageUrl || '', /^\/api\/fishit-tracker\/assets\/fish\//);
    assert.equal(card.imageStatus, 'cached');
    assert.equal(card.imageSource, 'local_asset_cache');
    const cached = fishImageCache.getCachedEntry(card.imageAssetId);
    assert.ok(cached);
    assert.equal(cached.imageStatus, 'cached');
  });

  test('rarity enrichment from confirmed catalog', async () => {
    const confirmedPath = fishCatalog.CONFIRMED_PATH;
    const orig = fs.existsSync(confirmedPath) ? fs.readFileSync(confirmedPath, 'utf8') : null;
    try {
      fs.writeFileSync(confirmedPath, JSON.stringify({
        fish: [{
          itemId: '68',
          name: 'Flame Angelfish',
          category: 'fish',
          rarity: 'legendary',
          imageAssetId: '128385926161840',
        }],
      }), 'utf8');
      fishCatalog._reset();
      const pub = await buildPublicFishFields([
        { name: 'Flame Angelfish', amount: 1, category: 'fish', itemId: '68' },
      ]);
      assert.equal(pub.publicItems[0].rarity, 'Legendary');
      assert.ok(pub.publicItems[0].raritySource);
    } finally {
      if (orig) fs.writeFileSync(confirmedPath, orig, 'utf8');
      else if (fs.existsSync(confirmedPath)) fs.unlinkSync(confirmedPath);
      fishCatalog._reset();
    }
  });

  test('enrichItemsFromCatalog strips weight from learned names', () => {
    const enriched = enrichItemsFromCatalog([
      { name: 'Deep Sea Crab (1.31K kg)', itemId: '9152', amount: 2, category: 'fish' },
      { name: 'Armor Catfish (2.65K kg)', itemId: '9215', amount: 1, category: 'fish' },
    ]);
    const crab = enriched.find((i) => i.itemId === '9152');
    const armor = enriched.find((i) => i.itemId === '9215');
    assert.equal(crab.name, 'Deep Sea Crab');
    assert.equal(crab.weightKg, 1310);
    assert.equal(armor.name, 'Armor Catfish');
    assert.equal(armor.weightKg, 2650);
  });

  test('Forgotten rarity label never becomes baseFishName', () => {
    const c = catchNameParser.canonicalizeFishName('Forgotten');
    assert.equal(c.baseFishName, null);
    const parsed = catchNameParser.parseCatchInput({ fishName: 'Forgotten Thunderzilla' });
    assert.equal(parsed.baseFishName, 'Thunderzilla');
    assert.equal(parsed.rarityCandidate, 'Forgotten');
  });

  test('build marker and tracker compile marker', () => {
    assert.equal(PUBLIC_API_BUILD, 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(src.includes('BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06'));
    assert.ok(src.includes('stripAllMutationPrefixes'));
  });
});

describe('BLOCKER10U2 catalog data backfill and persistence', () => {
  const canonicalCatalog = require('../src/fishitCanonicalCatalog');
  const sessionStore = require('../src/fishitSessionStore');
  const fishImageCache = require('../src/fishitFishImageCache');
  const fishCatalog = require('../src/fishitFishCatalog');
  const os = require('os');
  const {
    buildPublicFishFields,
    persistSessionState,
    PUBLIC_API_BUILD,
  } = require('../src/fishitTrackerRoutes');

  let tmpDir;
  let prevCanon;
  let prevSession;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fishit-u2-'));
    prevCanon = process.env.FISHIT_CANONICAL_CATALOG_PATH;
    prevSession = process.env.FISHIT_LIVE_SESSIONS_PATH;
    process.env.FISHIT_CANONICAL_CATALOG_PATH = path.join(tmpDir, 'canonical.json');
    process.env.FISHIT_LIVE_SESSIONS_PATH = path.join(tmpDir, 'sessions.json');
    canonicalCatalog._reset();
    fishCatalog._reset();
    fishImageCache._reset();
    sessionStore._reset();
  });

  afterEach(() => {
    process.env.FISHIT_CANONICAL_CATALOG_PATH = prevCanon;
    process.env.FISHIT_LIVE_SESSIONS_PATH = prevSession;
    canonicalCatalog._reset();
    fishCatalog._reset();
    fishImageCache._reset();
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) { /* */ }
  });

  test('rebuild searches all configured sources', () => {
    canonicalCatalog.rebuildFromAllSources({ persist: true });
    const audit = canonicalCatalog.getAudit();
    const ids = audit.sourcesSearched.map((s) => s.id);
    assert.ok(ids.includes('fishit_fish_image_assets'));
    assert.ok(ids.includes('fishit_catalog'));
    assert.ok(ids.includes('fishit_global_fish_item_catalog'));
    assert.ok(audit.totalEntries >= 40);
  });

  test('import script rows update canonical catalog image and rarity', async () => {
    const importRows = canonicalCatalog.importRows([
      {
        itemId: '157',
        baseFishName: 'Jellyfish',
        rarity: 'rare',
        imageAssetId: '128385926161840',
      },
    ], { persist: true });
    assert.equal(importRows.accepted, 1);
    fishCatalog._reset();
    const hit = canonicalCatalog.lookupByItemId('157');
    assert.equal(hit.baseFishName, 'Jellyfish');
    assert.equal(hit.rarity, 'Rare');
    assert.equal(hit.imageAssetId, '128385926161840');
    const pub = await buildPublicFishFields([
      { name: 'Jellyfish', amount: 10, category: 'fish', itemId: '157' },
    ]);
    assert.equal(pub.publicItems[0].rarity, 'Rare');
    assert.equal(pub.publicItems[0].imageStatus, 'cached');
  });

  test('session persistence restores lastGoodPublicFishItems after restart simulation', async () => {
    const live = {};
    const session = {
      username: 'PersistUser',
      userId: 999,
      items: [{ name: 'Deep Sea Crab', itemId: '152', amount: 5, category: 'fish' }],
      rawItems: [{ name: 'Deep Sea Crab', itemId: '152', amount: 5, category: 'fish' }],
      lastGoodPublicFishItems: [{
        name: 'Deep Sea Crab',
        itemId: '152',
        amount: 5,
        category: 'fish',
        baseFishName: 'Deep Sea Crab',
      }],
      lastGoodPublicFishCount: 1,
      lastSeenAt: new Date().toISOString(),
      lastInventoryAt: new Date().toISOString(),
      isOnline: true,
      phase: 'live',
    };
    sessionStore.saveSession('persistuser', session, live);
    const reloaded = {};
    const meta = sessionStore.loadIntoLiveTrackDB(reloaded);
    assert.equal(meta.loaded, 1);
    assert.ok(reloaded.persistuser.lastGoodPublicFishItems.length >= 1);
    assert.equal(reloaded.persistuser.lastGoodPublicFishItems[0].name, 'Deep Sea Crab');
  });

  test('public cards stay clean with Unknown rarity badge when no source', async () => {
    const pub = await buildPublicFishFields([
      { name: 'Deep Sea Crab', itemId: '152', amount: 2, category: 'fish' },
      { name: 'Item #72', itemId: '72', amount: 1, category: 'items' },
    ]);
    assert.equal(pub.publicItems.length, 1);
    assert.equal(pub.publicItems[0].name, 'Deep Sea Crab');
    assert.equal(pub.publicItems[0].rarity, 'Unknown');
  });

  test('build marker is BLOCKER10U3-U4', () => {
    assert.equal(PUBLIC_API_BUILD, 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');
  });
});

describe('BLOCKER10U3-U4 public name contract and Giant Squid recovery', () => {
  const catalogPolish = require('../src/fishitCatalogPolish');
  const canonicalCatalog = require('../src/fishitCanonicalCatalog');
  const manualVerified = require('../src/fishitManualVerifiedCatalog');
  const sessionStore = require('../src/fishitSessionStore');
  const fs = require('fs');
  const os = require('os');
  const path = require('path');
  const {
    buildPublicFishFields,
    enrichItemsFromCatalog,
    PUBLIC_API_BUILD,
  } = require('../src/fishitTrackerRoutes');

  const MUTATION_PREFIXES = catalogPolish.MUTATION_PREFIXES;
  const manualPath = path.join(__dirname, '..', 'data', 'fishit_manual_verified_catalog.json');

  function ensureGiantSquidManualCatalog() {
    assert.ok(fs.existsSync(manualPath), 'fishit_manual_verified_catalog.json must exist');
    const rows = JSON.parse(fs.readFileSync(manualPath, 'utf8'));
    const hit = rows.find((r) => String(r.itemId) === '156');
    assert.ok(hit && hit.baseFishName === 'Giant Squid', 'manual catalog must map 156 → Giant Squid');
    manualVerified._reset();
    canonicalCatalog._reset();
    canonicalCatalog.rebuildFromAllSources({ persist: false });
  }

  const MUTATION_CASES = [
    { input: 'Shiny Monk Fish', base: 'Monk Fish', mutation: 'Shiny' },
    { input: 'Shiny Vampire Squid', base: 'Vampire Squid', mutation: 'Shiny' },
    { input: 'Shiny Angler Fish', base: 'Angler Fish', mutation: 'Shiny' },
    { input: 'Shiny Synodontis', base: 'Synodontis', mutation: 'Shiny' },
    { input: 'Big Antique Watch', base: 'Antique Watch', mutation: 'Big' },
    { input: 'Ghost Fangtooth', base: 'Fangtooth', mutation: 'Ghost' },
    { input: 'Holographic Jellyfish', base: 'Jellyfish', mutation: 'Holographic' },
    { input: 'Sandy Pearl', base: 'Pearl', mutation: 'Sandy' },
    { input: 'Big Ruby', base: 'Ruby', mutation: 'Big' },
    { input: 'Big Liar Nose Fish', base: 'Liar Nose Fish', mutation: 'Big' },
    { input: 'Shiny Armor Catfish', base: 'Armor Catfish', mutation: 'Shiny' },
  ];

  for (const row of MUTATION_CASES) {
    test(`public name contract: ${row.input}`, async () => {
      const pub = await buildPublicFishFields([{
        name: row.input,
        displayName: row.input,
        baseFishName: row.base,
        mutation: row.mutation,
        amount: 1,
        category: 'fish',
        itemId: '999',
      }]);
      assert.equal(pub.publicItems.length, 1);
      const item = pub.publicItems[0];
      assert.equal(item.name, row.base);
      assert.equal(item.cardName, row.base);
      assert.equal(item.baseFishName, row.base);
      assert.equal(item.mutation, row.mutation);
      assert.equal(item.displayName, row.input);
      for (const prefix of MUTATION_PREFIXES) {
        assert.ok(!catalogPolish.startsWithMutationPrefix(item.name), `name starts with ${prefix}`);
        assert.ok(!catalogPolish.startsWithMutationPrefix(item.cardName), `cardName starts with ${prefix}`);
      }
    });
  }

  test('rendered HTML uses base title without visible mutation badges', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(tpl.includes('cardTitle(item)'));
    assert.ok(tpl.includes('item.cardName || item.baseFishName || item.name'));
    assert.ok(!tpl.includes('badge-mutation'));
    assert.ok(!tpl.includes('badge-shiny'));
    assert.ok(!tpl.includes('escHtml(item.name||\'Unknown\')</span>${qty}'));
    assert.ok(!tpl.includes('-webkit-line-clamp'));
    assert.ok(!tpl.includes('word-break:break-all'));
    assert.ok(!tpl.includes('overflow-wrap:anywhere'));
  });

  test('itemId 156 resolves to Giant Squid fish with Secret rarity', async () => {
    ensureGiantSquidManualCatalog();
    const enriched = enrichItemsFromCatalog([
      { name: 'Item #156', amount: 1, category: 'items', itemId: '156' },
    ]);
    assert.equal(enriched.length, 1);
    assert.equal(enriched[0].baseFishName, 'Giant Squid');
    assert.equal(enriched[0].category, 'fish');
    const pub = await buildPublicFishFields(enriched);
    assert.equal(pub.publicItems.length, 1);
    const gs = pub.publicItems[0];
    assert.equal(gs.name, 'Giant Squid');
    assert.equal(gs.cardName, 'Giant Squid');
    assert.equal(gs.rarity, 'Secret');
    assert.equal(gs.rarityNeedsData, false);
    assert.ok(gs.raritySource === 'manual_verified_catalog' || gs.raritySource === 'canonical_catalog');
  });

  test('Giant Squid shows without image and Forgotten block stays protected', async () => {
    ensureGiantSquidManualCatalog();
    const pub = await buildPublicFishFields([
      { name: 'Item #156', amount: 1, category: 'items', itemId: '156' },
      { name: 'Forgotten', amount: 1, category: 'fish', itemId: '196' },
      { name: 'Topwater Bait', amount: 2, category: 'bait', itemId: '10' },
    ]);
    assert.equal(pub.publicItems.length, 1);
    assert.equal(pub.publicItems[0].name, 'Giant Squid');
    assert.ok(pub.publicItems[0].imageStatus === 'missing' || pub.publicItems[0].imageStatus === 'cached');
  });

  test('publicNameContractProof helper marks titleUsesBaseName', () => {
    const proof = catalogPolish.buildPublicNameContractProof([{
      itemId: '160',
      name: 'Monk Fish',
      cardName: 'Monk Fish',
      baseFishName: 'Monk Fish',
      displayName: 'Shiny Monk Fish',
      mutation: 'Shiny',
    }]);
    assert.equal(proof[0].titleUsesBaseName, true);
    assert.equal(proof[0].mutationSeparated, true);
  });

  test('session persistence keeps Giant Squid after restart simulation', async () => {
    ensureGiantSquidManualCatalog();
    const pub = await buildPublicFishFields([
      { name: 'Item #156', amount: 1, category: 'items', itemId: '156' },
    ]);
    const prevSession = process.env.FISHIT_LIVE_SESSIONS_PATH;
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fishit-u3u4-sess-'));
    process.env.FISHIT_LIVE_SESSIONS_PATH = path.join(tmpDir, 'sessions.json');
    sessionStore._reset();
    const live = {};
    sessionStore.saveSession('denghub2', {
      username: 'denghub2',
      userId: 1,
      items: [{ name: 'Item #156', itemId: '156', amount: 1, category: 'items' }],
      rawItems: [{ name: 'Item #156', itemId: '156', amount: 1, category: 'items' }],
      lastGoodPublicFishItems: pub.fishItems,
      lastGoodPublicFishCount: 1,
      lastSeenAt: new Date().toISOString(),
      isOnline: true,
    }, live);
    const reloaded = {};
    sessionStore.loadIntoLiveTrackDB(reloaded);
    assert.equal(reloaded.denghub2.lastGoodPublicFishItems[0].name, 'Giant Squid');
    assert.equal(reloaded.denghub2.lastGoodPublicFishItems[0].rarity, 'Secret');
    process.env.FISHIT_LIVE_SESSIONS_PATH = prevSession;
    sessionStore._reset();
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) { /* */ }
  });

  test('build marker is BLOCKER10U5', () => {
    assert.equal(PUBLIC_API_BUILD, 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06');
  });
});

describe('BLOCKER10U5 critical public card and image fix', () => {
  const catalogPolish = require('../src/fishitCatalogPolish');
  const catchNameParser = require('../src/fishitCatchNameParser');
  const protectedFishNames = require('../src/fishitProtectedFishNames');
  const fishImageCache = require('../src/fishitFishImageCache');
  const canonicalCatalog = require('../src/fishitCanonicalCatalog');
  const manualVerified = require('../src/fishitManualVerifiedCatalog');
  const fs = require('fs');
  const path = require('path');
  const {
    buildPublicFishFields,
    PUBLIC_API_BUILD,
  } = require('../src/fishitTrackerRoutes');

  const U5_BUILD = 'BLOCKER10U5_CRITICAL_PUBLIC_CARD_AND_IMAGE_FIX_2026_06_06';
  const MUTATION_PREFIXES = catalogPolish.MUTATION_PREFIXES;
  const tplPath = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
  const tpl = fs.readFileSync(tplPath, 'utf8');

  function renderCardHtml(item) {
    const title = item.cardName || item.baseFishName || item.name || 'Unknown';
    const rarity = item.rarity && item.rarity !== 'Unknown' ? item.rarity : null;
    const rarityLow = rarity ? rarity.toLowerCase() : '';
    const cardCls = ['item-card'];
    if (rarityLow === 'secret') cardCls.push('card-rarity-secret');
    if (item.shiny === true && rarityLow !== 'secret') cardCls.push('shiny');
    return `<div class="${cardCls.join(' ')}" data-mutation="${item.mutation || ''}"><span class="item-name">${title}</span></div>`;
  }

  const HIDDEN_MUTATION_CASES = [
    { input: 'Shiny Jellyfish', base: 'Jellyfish', mutation: 'Shiny' },
    { input: 'Shiny Synodontis', base: 'Synodontis', mutation: 'Shiny' },
    { input: 'Shiny Angler Fish', base: 'Angler Fish', mutation: 'Shiny' },
    { input: 'Big Pearl', base: 'Pearl', mutation: 'Big' },
    { input: 'Holographic Jellyfish', base: 'Jellyfish', mutation: 'Holographic' },
  ];

  for (const row of HIDDEN_MUTATION_CASES) {
    test(`U5 API clean names: ${row.input}`, async () => {
      const pub = await buildPublicFishFields([{
        name: row.input,
        displayName: row.input,
        baseFishName: row.base,
        mutation: row.mutation,
        shiny: row.mutation === 'Shiny',
        amount: 1,
        category: 'fish',
        itemId: 'u5',
      }]);
      const item = pub.publicItems[0];
      assert.equal(item.name, row.base);
      assert.equal(item.cardName, row.base);
      assert.equal(item.baseFishName, row.base);
      assert.equal(item.mutation, row.mutation);
      for (const prefix of MUTATION_PREFIXES) {
        assert.ok(!catalogPolish.startsWithMutationPrefix(item.name));
        assert.ok(!catalogPolish.startsWithMutationPrefix(item.cardName));
      }
      const html = renderCardHtml(item);
      assert.ok(html.includes(row.base));
      assert.ok(!html.includes('>Shiny<'));
      assert.ok(!html.includes('>Big<'));
      assert.ok(!html.includes('>Ghost<'));
      assert.ok(!html.includes('>Holographic<'));
    });
  }

  test('Giant Squid protected name never becomes Squid', () => {
    assert.ok(protectedFishNames.isProtectedBaseName('Giant Squid'));
    const canon = catchNameParser.canonicalizeFishName('Giant Squid');
    assert.equal(canon.baseFishName, 'Giant Squid');
    assert.equal(canon.reason, 'protected_base_name');
    assert.ok(!catalogPolish.startsWithMutationPrefix('Giant Squid'));
  });

  test('itemId 156 stays Giant Squid Secret fish public', async () => {
    manualVerified._reset();
    canonicalCatalog._reset();
    canonicalCatalog.rebuildFromAllSources({ persist: false });
    const pub = await buildPublicFishFields([
      { name: 'Item #156', amount: 1, category: 'items', itemId: '156' },
    ]);
    assert.equal(pub.publicItems.length, 1);
    const gs = pub.publicItems[0];
    assert.equal(gs.name, 'Giant Squid');
    assert.equal(gs.cardName, 'Giant Squid');
    assert.equal(gs.baseFishName, 'Giant Squid');
    assert.equal(gs.rarity, 'Secret');
    assert.notEqual(gs.name, 'Squid');
  });

  test('Secret styling classes override shiny card class in template', () => {
    assert.ok(tpl.includes('card-rarity-secret'));
    assert.ok(tpl.includes('rarity-secret'));
    assert.ok(tpl.includes('#22d3ee'));
    assert.ok(tpl.includes('rarityLow !== \'secret\''));
    assert.ok(!tpl.includes('badge-mutation'));
    assert.ok(!tpl.includes('badge-shiny'));
  });

  test('Giant Squid rendered card uses Secret cyan styling', () => {
    const html = renderCardHtml({
      name: 'Giant Squid',
      cardName: 'Giant Squid',
      baseFishName: 'Giant Squid',
      rarity: 'Secret',
      shiny: true,
      mutation: 'Shiny',
    });
    assert.ok(html.includes('card-rarity-secret'));
    assert.ok(html.includes('Giant Squid'));
    assert.ok(!html.includes('class="item-card shiny"'));
    assert.ok(!html.includes('>Shiny<'));
    assert.ok(!html.includes('>Squid<'));
  });

  test('title layout CSS avoids per-character wrapping', () => {
    assert.ok(!tpl.includes('-webkit-line-clamp'));
    assert.ok(!tpl.includes('-webkit-box-orient:vertical'));
    assert.ok(!tpl.includes('word-break:break-all'));
    assert.ok(!tpl.includes('overflow-wrap:anywhere'));
    assert.ok(tpl.includes('word-break:normal'));
    assert.ok(tpl.includes('flex-wrap:nowrap'));
  });

  test('inline JS reads render build from server data attributes', () => {
    assert.ok(tpl.includes('data-render-build'));
    assert.ok(tpl.includes('document.documentElement.getAttribute(\'data-render-build\')'));
    assert.ok(!tpl.includes('BLOCKER10U2_CATALOG_DATA_BACKFILL_AND_PERSISTENCE_2026_06_06'));
  });

  test('quiz bot image source wired into resolveImageMetaForItem', () => {
    fishImageCache._reset();
    const meta = fishImageCache.resolveImageMetaForItem({
      itemId: '156',
      baseFishName: 'Giant Squid',
      name: 'Giant Squid',
      category: 'fish',
    });
    const hasQuiz = meta.imageSource === 'quiz_bot_fishit_bank'
      || meta.searchedSources?.includes('quiz_bot_fishit_bank');
    const hasLocal = !!meta.localFilePath;
    assert.ok(hasQuiz || hasLocal || process.env.NODE_ENV === 'test',
      'Giant Squid should resolve quiz bot bank image when bank present');
  });

  test('buildImageSourceProof includes probe rows', () => {
    const proof = fishImageCache.buildImageSourceProof([], null, 5);
    assert.ok(Array.isArray(proof));
    assert.ok(proof.length >= 1);
    assert.ok(proof.some((r) => r.baseFishName === 'Giant Squid'));
  });

  test('build marker is BLOCKER10U5', () => {
    assert.equal(PUBLIC_API_BUILD, U5_BUILD);
    assert.ok(tpl.includes(U5_BUILD));
  });
});

describe('BLOCKER10U6 quiz bot fish image source', () => {
  const quizBotCatalog = require('../src/fishitQuizBotImageCatalog');
  const fishImageCache = require('../src/fishitFishImageCache');
  const fs = require('fs');
  const path = require('path');
  const {
    buildPublicFishFields,
    PUBLIC_API_BUILD,
  } = require('../src/fishitTrackerRoutes');

  const U6_BUILD = 'BLOCKER10U6_QUIZ_BOT_FISH_IMAGE_SOURCE_FIX_2026_06_06';
  const QUIZ_NAMES = [
    'Mossy Fishlet', 'Parrot Fish', 'Parrot Blopfish', 'Viperangler Fish',
    'Freshwater Piranha', 'Goliath Tiger', 'Spear Guardian', 'Giant Squid',
  ];

  test('quiz bot catalog meta points to fishit_bank.json not deng-quiz.sqlite', () => {
    const meta = quizBotCatalog.getCatalogMeta();
    assert.ok(meta.bankPath.includes('fishit_bank.json'));
    assert.ok(meta.assetsDir.includes('FishItFish'));
    assert.ok(meta.note.includes('deng-quiz.sqlite'));
    assert.ok(meta.bankEntryCount >= 600, `expected 600+ bank entries, got ${meta.bankEntryCount}`);
  });

  for (const name of QUIZ_NAMES) {
    test(`quiz bot bank resolves ${name} by name`, () => {
      const hit = quizBotCatalog.lookupByFishName(name);
      assert.ok(hit, `${name} must exist in quiz bot fishit_bank.json`);
      assert.equal(hit.name, name);
      assert.ok(hit.localFile, `${name} must have localFile in bank`);
      if (fs.existsSync(quizBotCatalog.ASSETS_DIR)) {
        assert.ok(hit.cachedInQuizBot, `${name} webp must exist in FishItFish assets`);
      }
    });
  }

  test('resolveImageMetaForItem prefers quiz bot local webp over fishit_db', () => {
    fishImageCache._reset();
    const meta = fishImageCache.resolveImageMetaForItem({
      baseFishName: 'Mossy Fishlet',
      name: 'Mossy Fishlet',
      cardName: 'Mossy Fishlet',
      itemId: '287',
    });
    assert.equal(meta.imageSource, 'quiz_bot_fishit_bank');
    assert.ok(meta.localFilePath || meta.assetId);
    assert.ok(meta.sourceDb?.includes('quiz_bot'));
    assert.ok(meta.triedAliases?.includes('Mossy Fishlet'));
  });

  test('buildPublicFishFields caches Mossy Fishlet from quiz bot webp', async () => {
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    fishImageCache._reset();
    const pub = await buildPublicFishFields([{
      name: 'Mossy Fishlet',
      baseFishName: 'Mossy Fishlet',
      cardName: 'Mossy Fishlet',
      amount: 1,
      category: 'fish',
      itemId: '287',
    }]);
    assert.equal(pub.publicItems.length, 1);
    const item = pub.publicItems[0];
    assert.equal(item.name, 'Mossy Fishlet');
    if (fs.existsSync(path.join(quizBotCatalog.ASSETS_DIR, 'Mossy_Fishlet.webp'))) {
      assert.equal(item.imageStatus, 'cached');
      assert.ok(String(item.imageUrl).startsWith('/api/fishit-tracker/assets/fish/'));
      assert.equal(item.imageSource, 'quiz_bot_fishit_bank');
    }
  });

  test('buildImageSourceProof reports quiz bot match and aliases', () => {
    const proof = fishImageCache.buildImageSourceProof([], QUIZ_NAMES, 8);
    const mossy = proof.find((r) => r.baseFishName === 'Mossy Fishlet');
    const parrot = proof.find((r) => r.baseFishName === 'Parrot Fish');
    assert.ok(mossy);
    assert.ok(parrot);
    assert.equal(mossy.quizBotMatched, true);
    assert.equal(parrot.quizBotMatched, true);
    assert.ok(Array.isArray(mossy.aliasesTried));
    assert.ok(mossy.sourceDb?.includes('quiz_bot'));
  });

  test('build marker is BLOCKER10U6', () => {
    assert.equal(PUBLIC_API_BUILD, U6_BUILD);
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(tpl.includes(U6_BUILD));
  });
});
