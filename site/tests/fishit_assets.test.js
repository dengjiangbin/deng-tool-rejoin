'use strict';
/**
 * v1.0.8 unit tests: rod-image resolver, Fish It pure helpers, and the
 * dashboard package summary aggregation. These need no DB / network.
 */

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

process.env.NODE_ENV = 'test';
// monitorRoutes pulls in ./db which requires these at import time.
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';

const rodAssets = require('../src/fishitRodAssets');

describe('Rod asset resolver (channel 1483265484215287909)', () => {
  test('returns a real, non-fallback image URL for each rod', () => {
    for (const key of ['ghostfinn', 'element', 'diamond']) {
      const url = rodAssets.rodImageUrl(key);
      assert.ok(typeof url === 'string', `${key} url is a string`);
      assert.ok(/^https?:\/\//.test(url), `${key} url is http(s)`);
      assert.ok(!/fallback/i.test(url), `${key} url is not a fallback`);
      // Sourced from the bot's rod custom emoji on the Discord CDN.
      assert.ok(url.includes('cdn.discordapp.com/emojis/'), `${key} url is the channel emoji`);
    }
  });

  test('labels are the human rod names', () => {
    assert.equal(rodAssets.rodLabel('ghostfinn'), 'Ghostfinn Rod');
    assert.equal(rodAssets.rodLabel('element'), 'Element Rod');
    assert.equal(rodAssets.rodLabel('diamond'), 'Diamond Rod');
  });

  test('unknown rod returns null image (never a wrong image)', () => {
    assert.equal(rodAssets.rodImageUrl('nope'), null);
  });
});

describe('Fish It pure helpers', () => {
  // Load fishitDb with a non-existent DB path so the DB layer is inert; the
  // pure helpers (speciesKey/formatWeight/forgottenTotal) don't touch SQLite.
  process.env.FISHIT_DB_PATH = '/nonexistent/deng-fish-it.sqlite';
  const fishit = require('../src/fishitDb');

  test('speciesKey slugifies names', () => {
    assert.equal(fishit.speciesKey('Strawberry Shenanigans'), 'strawberry-shenanigans');
    assert.equal(fishit.speciesKey('Thunderzilla'), 'thunderzilla');
    assert.equal(fishit.speciesKey('  King   Crab  '), 'king-crab');
  });

  test('formatWeight produces compact strings', () => {
    assert.equal(fishit.formatWeight(1_100_000), '1.1M');
    assert.equal(fishit.formatWeight(165_900), '165.9K');
    assert.equal(fishit.formatWeight(700), '700');
    assert.equal(fishit.formatWeight(0), null);
    assert.equal(fishit.formatWeight(-5), null);
  });

  test('forgottenTotal avoids double-counting Thunderzilla / Sea Eater', () => {
    // Thunderzilla appears in both the map and the dedicated counter — count once.
    assert.equal(fishit.forgottenTotal({ forgottenFish: { Thunderzilla: 5, Iridesca: 3 }, thunderzilla: 5, seaEater: 0 }), 8);
    // Sea Eater only in the dedicated counter (legacy) — included once.
    assert.equal(fishit.forgottenTotal({ forgottenFish: { Iridesca: 3 }, thunderzilla: 0, seaEater: 2 }), 5);
  });
});

describe('Dashboard package summary aggregation', () => {
  const { aggregatePackageSummary } = require('../src/monitorRoutes').__test__;

  test('8 configured packages all dead → TOTAL 8 / ONLINE 0 / DEAD 8', () => {
    const rows = Array.from({ length: 8 }, (_, i) => ({ package_name: `com.pkg${i}`, state: 'Dead', ram_mb: 0 }));
    const s = aggregatePackageSummary(rows);
    assert.equal(s.total, 8);
    assert.equal(s.online, 0);
    assert.equal(s.dead, 8);
  });

  test('8 packages, 3 online → TOTAL 8 / ONLINE 3 / DEAD 5', () => {
    const rows = [
      { state: 'Online' }, { state: 'Online' }, { state: 'Online' },
      { state: 'Dead' }, { state: 'Dead' }, { state: 'Launching' }, { state: 'Joining' }, { state: 'No Heartbeat' },
    ];
    const s = aggregatePackageSummary(rows);
    assert.equal(s.total, 8);
    assert.equal(s.online, 3);
    assert.equal(s.dead, 5);
  });

  test('empty package list → 0 / 0 / 0 (no crash)', () => {
    const s = aggregatePackageSummary([]);
    assert.equal(s.total, 0);
    assert.equal(s.online, 0);
    assert.equal(s.dead, 0);
  });

  test('stale/unknown states count as dead, never online', () => {
    const rows = [{ state: 'Unknown' }, { state: 'Stale' }, { state: 'Online' }];
    const s = aggregatePackageSummary(rows);
    assert.equal(s.total, 3);
    assert.equal(s.online, 1);
    assert.equal(s.dead, 2);
  });
});
