'use strict';

// P0 2026-06-19 — Lua warn streak + worker presence disk sync + inventory text-only UI.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const PRIVATE_LUA = path.join('C:', 'Users', 'Administrator', 'Desktop', 'DENG PRIVATE SOURCE', 'fishtracker', 'tracker.lua');
const hasPrivate = fs.existsSync(PRIVATE_LUA);
const WORKER_SRC = path.join(__dirname, '..', 'src', 'trackerWorkerApp.js');
const TRACKER_SRC = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');

describe('P0 warn streak + denghub2 freshness + inventory text-only (2026-06-19)', () => {
  test('worker tick reloads live sessions from disk before dirty/presence sweep', () => {
    const worker = fs.readFileSync(WORKER_SRC, 'utf8');
    const tick = worker.match(/async function tick\(\)[\s\S]*?^}/m);
    assert.ok(tick, 'tick() must exist');
    assert.match(tick[0], /routes\.syncLiveTrackFromDisk\(\)/);
    const syncIdx = tick[0].indexOf('syncLiveTrackFromDisk');
    const dirtyIdx = tick[0].indexOf('computeDirty');
    assert.ok(syncIdx > 0 && dirtyIdx > syncIdx, 'disk sync must run before computeDirty');
  });

  test('inventory section badges are text-only neutral (no dot / no green-red)', () => {
    const src = fs.readFileSync(TRACKER_SRC, 'utf8');
    assert.match(src, /function formatInventoryUploadLabel/);
    assert.match(src, /Inventory updated \$\{ageText\}/);
    assert.match(src, /Inventory stale \$\{ageText\}/);
    assert.match(src, /Inventory waiting for upload/);
    const patch = src.match(/function patchInventoryUploadIndicatorDom\(root, entry\) \{[\s\S]*?\n {2}\}/);
    assert.ok(patch, 'patchInventoryUploadIndicatorDom missing');
    assert.match(patch[0], /dotEl\.remove\(\)/);
    assert.match(patch[0], /formatInventoryUploadLabel/);
    assert.match(patch[0], /classList\.add\('is-neutral'\)/);
    const html = src.match(/function buildSectionUploadIndicatorHtml\([\s\S]*?\n {2}\}/);
    assert.ok(html);
    assert.doesNotMatch(html[0], /data-inventory-upload-dot|status-dot/);
  });

  test('frontend lane ages use server _auth timestamps only (no page-load timer seed)', () => {
    const src = fs.readFileSync(TRACKER_SRC, 'utf8');
    assert.match(src, /function backendInventoryAgeSeconds[\s\S]*?_auth\.lastRealInventoryAt/);
    assert.match(src, /function backendStatsAgeSeconds[\s\S]*?_auth\.lastRealLeaderstatsAt/);
    assert.match(src, /function backendPresenceAgeSeconds[\s\S]*?_auth\.lastRealStatusAt/);
    assert.match(src, /function seedTimersFromBackend\(_entry\) \{ \/\* no-op/);
  });

  test('Lua: local defer is soft-success — no SYNC_UPLOAD warn streak on healthy defer', { skip: !hasPrivate }, () => {
    const lua = fs.readFileSync(PRIVATE_LUA, 'utf8');
    assert.doesNotMatch(lua, /warn\(LOG, \("SYNC_UPLOAD warn streak=/);
    assert.match(lua, /leaderstatsDeferredLocal/);
    assert.match(lua, /inventoryDeferredLocal/);
    const loop = lua.match(/function LiveSafe\.ensureLightSyncLoop\(\)[\s\S]*?\nend\n\nfunction LiveSafe\.startReplionRetryLoop/m);
    assert.ok(loop);
    assert.match(loop[0], /SYNC_UPLOAD_FAIL streak=/);
    assert.match(loop[0], /SYNC_UPLOAD_DEFER streak=/);
    assert.match(lua, /leaderstatsDeferredLocal[\s\S]*soft-success/);
  });

  test('Lua: real HTTP bad response still logs honestly', { skip: !hasPrivate }, () => {
    const lua = fs.readFileSync(PRIVATE_LUA, 'utf8');
    assert.match(lua, /function HttpDash\.logHttpResponseBad/);
    assert.match(lua, /HTTP_RESPONSE_BAD lane=/);
    const fn = lua.match(/function HttpDash\.postRequiredLeaderstats\(encoded, logMeta\)[\s\S]*?\nend/);
    assert.ok(fn);
    assert.match(fn[0], /isLocalDeferReason\(uploadWhy\)/);
    assert.match(fn[0], /logHttpResponseBad\("required_leaderstats"/);
  });
});
