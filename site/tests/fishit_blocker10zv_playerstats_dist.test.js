'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('node:child_process');
const { resolveRawTrackerSourcePath } = require('../../scripts/trackerRawSourcePath');

const ROOT = path.join(__dirname, '..', '..');
const DIST_LUA = path.join(ROOT, 'dist', 'tracker.lua');
const BUILD = 'BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10';

describe('BLOCKER10ZV playerStats in real tracker source and dist', () => {
  test('private raw source contains buildPlayerStatsPayload and always uploads playerStats', () => {
    const rawPath = resolveRawTrackerSourcePath({ root: ROOT });
    assert.ok(rawPath, 'private raw tracker source must exist for release');
    const raw = fs.readFileSync(rawPath, 'utf8');
    assert.match(raw, /TRACKER_BUILD = "BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10"/);
    assert.match(raw, /buildPlayerStatsPayload/);
    assert.match(raw, /buildPlayerStatsDebugPayload/);
    assert.match(raw, /payload\.playerStats = buildPlayerStatsPayload\(\)/);
    assert.match(raw, /payload\.playerStatsDebug = buildPlayerStatsDebugPayload\(\)/);
    assert.match(raw, /parseCompactNumber/);
    assert.match(raw, /rarestFishChance/);
    assert.match(raw, /source = "missing"/);
    assert.doesNotMatch(raw, /scanPlayerGuiStatFallback/);
    assert.doesNotMatch(raw, /player_gui_fallback/);
    assert.doesNotMatch(raw, /payload\.quest/);
    assert.doesNotMatch(raw, /elementFlags/);
  });

  test('dist/tracker.lua is rebuilt and differs from raw source', () => {
    const rawPath = resolveRawTrackerSourcePath({ root: ROOT });
    assert.ok(fs.existsSync(DIST_LUA), DIST_LUA);
    const dist = fs.readFileSync(DIST_LUA, 'utf8');
    assert.ok(dist.includes(BUILD));
    assert.ok(Buffer.byteLength(dist, 'utf8') > 4096);
    if (rawPath) {
      const raw = fs.readFileSync(rawPath, 'utf8');
      assert.notEqual(raw.trim(), dist.trim());
    }
  });

  test('dist validation passes after rebuild', () => {
    const out = execFileSync(process.execPath, [
      path.join(ROOT, 'scripts', 'validate_luraph_dist.js'),
    ], { encoding: 'utf8' });
    assert.match(out, /DIST_TRACKER_VALIDATION OK/);
  });
});
