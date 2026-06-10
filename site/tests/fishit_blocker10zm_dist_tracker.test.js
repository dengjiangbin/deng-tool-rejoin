'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('node:child_process');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const {
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  PROTECTED_DIST_REL_PATH,
} = require('../src/fishitTrackerLoadstring');
const {
  BLOCKER10ZM_DIST_TRACKER_LUA_PROTECTED_PUBLIC_LOADER_BUILD,
  BLOCKER10ZL_BUILD,
} = require('../src/fishitTrackerBuild');
const { buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');
const trackerRouter = require('../src/fishitTrackerRoutes');
const { auditFile } = require('../../scripts/audit_tracker_secrets');
const { RAW_TRACKER_LUA, testIfRawTracker, REPO_ROOT } = require('./helpers/trackerRawSource');

const ROOT = REPO_ROOT;
const DIST_LUA = path.join(ROOT, 'dist', 'tracker.lua');
const TRACKER_BUILD = 'BLOCKER10ZL_LURAPH_PROTECTED_RELEASE_2026_06_10';
const PUBLIC_LOADER = CLEAN_TRACKER_LOADSTRING;

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

function fishRow(overrides = {}) {
  return {
    kind: 'fish',
    itemId: '70',
    name: 'Clownfish',
    baseName: 'Clownfish',
    quantity: 2,
    tier: 1,
    rarity: 'Common',
    type: 'Fish',
    mutation: 'None',
    icon: 'rbxassetid://1234567890123',
    source: 'playerdata_gameitemdb',
    identityVerified: true,
    ...overrides,
  };
}

function stoneRow(type, itemId, qty = 1) {
  return {
    kind: 'stone',
    itemId: String(itemId),
    name: `${type} Enchant Stone`,
    stoneType: type,
    quantity: qty,
    icon: 'rbxassetid://9999999999999',
    source: 'playerdata_gameitemdb',
    identityVerified: true,
  };
}

function protectedUploadBody(overrides = {}) {
  return {
    username: 'B10ZMLive',
    userId: 61020,
    source: 'replion',
    isOnline: true,
    type: 'inventory_snapshot',
    trackerBuild: TRACKER_BUILD,
    inventorySource: 'playerdata_gameitemdb',
    fishItems: [fishRow()],
    stoneItems: [stoneRow('Normal', 10, 2)],
    playerDataGameItemDbProof: { uploadPath: 'playerdata_gameitemdb', build: TRACKER_BUILD },
    sourceTruth: gameItemDbPublic.defaultSourceTruth(),
    ...overrides,
  };
}

describe('BLOCKER10ZM protected dist/tracker.lua public loader', () => {
  test('build marker is BLOCKER10ZM', () => {
    assert.equal(
      BLOCKER10ZM_DIST_TRACKER_LUA_PROTECTED_PUBLIC_LOADER_BUILD,
      'BLOCKER10ZM_DIST_TRACKER_LUA_PROTECTED_PUBLIC_LOADER_2026_06_10',
    );
    assert.equal(BLOCKER10ZL_BUILD, TRACKER_BUILD);
  });

  testIfRawTracker('private raw tracker source compile validation passes when present', () => {
    const out = execFileSync(process.execPath, [
      path.join(ROOT, 'scripts', 'validate_tracker_compile.js'),
      RAW_TRACKER_LUA,
    ], { encoding: 'utf8' });
    assert.match(out, /TRACKER_COMPILE_VALIDATION OK/);
    assert.match(out, /BLOCKER10ZL_LURAPH_PROTECTED_RELEASE_2026_06_10/);
  });

  test('public loader points to dist/tracker.lua not raw root tracker.lua', () => {
    assert.equal(CLEAN_TRACKER_LOADSTRING, PUBLIC_LOADER);
    assert.equal(PROTECTED_DIST_RAW_URL, 'https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua');
    assert.equal(PROTECTED_DIST_REL_PATH, 'dist/tracker.lua');
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /\/main\/tracker\.lua"\)\)\(\)/);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /tracker\.luraph\.lua/);
    assert.equal(buildTrackerPageLocals().trackerLoadstring, CLEAN_TRACKER_LOADSTRING);
  });

  test('/tracker public page shows dist/tracker.lua loader only', async () => {
    const res = await request(makeApp()).get('/tracker').expect(200);
    assert.match(res.text, /dist\/tracker\.lua/);
    assert.doesNotMatch(res.text, /tracker\.luraph\.lua/);
    assert.doesNotMatch(res.text, /main\/tracker\.lua"\)\)\(\)/);
    assert.doesNotMatch(res.text, /\?t=/);
    assert.doesNotMatch(res.text, /Debug loader \(cache-busted\)/);
  });

  test('/tracker?debug=global does not expose raw root loader', async () => {
    const res = await request(makeApp()).get('/tracker?debug=global').expect(200);
    assert.match(res.text, /dist\/tracker\.lua/);
    assert.doesNotMatch(res.text, /main\/tracker\.lua\?t=/);
    assert.doesNotMatch(res.text, /loadstringDebugBox/);
  });

  testIfRawTracker('private raw dev source references dist/tracker.lua in usage comment', () => {
    const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
    assert.match(lua, /TRACKER_BUILD = "BLOCKER10ZL_LURAPH_PROTECTED_RELEASE_2026_06_10"/);
    assert.match(lua, /dist\/tracker\.lua/);
    assert.doesNotMatch(lua, /tracker\.luraph\.lua/);
  });

  testIfRawTracker('no secrets in private raw tracker source', () => {
    const audit = auditFile(RAW_TRACKER_LUA);
    assert.equal(audit.ok, true, audit.hits.join(', '));
  });

  test('dist/tracker.lua exists and differs from private raw source when present', () => {
    assert.ok(fs.existsSync(DIST_LUA), DIST_LUA);
    const dist = fs.readFileSync(DIST_LUA, 'utf8');
    assert.ok(dist.length > 4096);
    if (RAW_TRACKER_LUA) {
      const raw = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
      assert.notEqual(raw.trim(), dist.trim());
    }
  });

  test('dist/tracker.lua passes dist validation', () => {
    const out = execFileSync(process.execPath, [
      path.join(ROOT, 'scripts', 'validate_luraph_dist.js'),
    ], { encoding: 'utf8' });
    assert.match(out, /DIST_TRACKER_VALIDATION OK/);
  });

  test('no secrets in dist/tracker.lua', () => {
    const audit = auditFile(DIST_LUA);
    assert.equal(audit.ok, true, audit.hits.join(', '));
  });

  test('backend accepts protected playerdata_gameitemdb upload', async () => {
    const app = makeApp();
    const post = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send(protectedUploadBody())
      .expect(200);
    assert.equal(post.body.status, 'success');
    assert.notEqual(post.body.legacySnapshotIgnored, true);

    const get = await request(app)
      .get('/api/fishit-tracker/get-backpack/b10zmlive')
      .expect(200);
    assert.equal(get.body.inventorySource, 'playerdata_gameitemdb');
    assert.ok(Array.isArray(get.body.fishItems));
    assert.ok(get.body.fishItems.length >= 1);
  });

  test('backend rejects legacy snapshot from protected build', async () => {
    const app = makeApp();
    const post = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'B10ZMLegacy',
        userId: 61021,
        source: 'replion',
        isOnline: true,
        type: 'inventory_snapshot',
        trackerBuild: TRACKER_BUILD,
        items: [{ name: 'King Crab', count: 3, category: 'fish', itemId: '1' }],
      })
      .expect(200);
    assert.equal(post.body.legacySnapshotIgnored, true);
    assert.equal(post.body.ignoreReason, 'missing_playerdata_gameitemdb_inventorySource');
  });

  test('expectsPlayerDataGameItemDbPayload matches protected build', () => {
    assert.equal(gameItemDbPublic.expectsPlayerDataGameItemDbPayload({ trackerBuild: TRACKER_BUILD }), true);
    assert.equal(gameItemDbPublic.detectGameItemDbUpload({ inventorySource: 'playerdata_gameitemdb' }), true);
  });
});
