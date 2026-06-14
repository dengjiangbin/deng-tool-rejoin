'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

process.env.FISHIT_SESSION_SYNC_SAVE = '1';

const { isTrackerUploadPath, UPLOAD_POST_PATHS } = require('../src/trackerUploadPaths');
const { shouldProxyTrackerUpload } = require('../src/trackerUploadProxy');
const trackerConcurrencyGate = require('../src/trackerConcurrencyGate');
const fishitSessionStore = require('../src/fishitSessionStore');
const { isSessionlessPath, PUBLIC_BRAND_NAME, CANONICAL_PUBLIC_HOST } = require('../src/publicDomain');

describe('tracker process isolation', () => {
  beforeEach(() => {
    trackerConcurrencyGate._resetForTests();
    fishitSessionStore._reset();
  });

  test('isTrackerUploadPath matches Roblox upload POST endpoints only', () => {
    assert.equal(isTrackerUploadPath('POST', '/api/fishit-tracker/update-backpack'), true);
    assert.equal(isTrackerUploadPath('POST', '/api/tracker/update-catalog'), true);
    assert.equal(isTrackerUploadPath('GET', '/api/fishit-tracker/update-backpack'), false);
    assert.equal(isTrackerUploadPath('POST', '/login'), false);
    assert.equal(UPLOAD_POST_PATHS.size, 4);
  });

  test('shouldProxyTrackerUpload detects upload POST requests', () => {
    assert.equal(shouldProxyTrackerUpload({ method: 'POST', url: '/api/tracker/update-backpack' }), true);
    assert.equal(shouldProxyTrackerUpload({ method: 'GET', url: '/api/tracker/update-backpack' }), false);
  });

  test('isSessionlessPath includes tracker upload APIs', () => {
    assert.equal(isSessionlessPath('/api/fishit-tracker/update-backpack', 'POST'), true);
    assert.equal(isSessionlessPath('/api/tracker/summary', 'GET'), false);
    assert.equal(isSessionlessPath('/login'), false);
    assert.equal(isSessionlessPath('/auth/discord'), false);
  });

  test('tracker gate dedupes repeated jobs for same account', () => {
    let runs = 0;
    trackerConcurrencyGate.scheduleDeferredUploadWork('testuser', () => { runs += 1; });
    trackerConcurrencyGate.scheduleDeferredUploadWork('testuser', () => { runs += 1; });
    const stats = trackerConcurrencyGate.stats();
    assert.equal(stats.deferredPending, 1);
    assert.ok(stats.deferredSuperseded >= 1);
  });

  test('tracker gate enforces queue max', () => {
    process.env.TRACKER_QUEUE_MAX = '2';
    delete require.cache[require.resolve('../src/trackerConcurrencyGate')];
    const gate = require('../src/trackerConcurrencyGate');
    gate._resetForTests();
    gate.scheduleDeferredUploadWork('a', () => {});
    gate.scheduleDeferredUploadWork('b', () => {});
    gate.scheduleDeferredUploadWork('c', () => {});
    assert.equal(gate.stats().droppedJobs, 1);
    process.env.TRACKER_QUEUE_MAX = '1000';
    delete require.cache[require.resolve('../src/trackerConcurrencyGate')];
  });

  test('fishitSessionStore reloadIfChanged merges disk updates', () => {
    const live = {};
    fishitSessionStore.saveSession('demo', {
      username: 'Demo',
      userId: 1,
      items: [],
      rawItems: [],
      isOnline: true,
      lastSeenAt: new Date().toISOString(),
    }, live);
    const target = {};
    fishitSessionStore._invalidateReloadCursorForTests();
    const reloaded = fishitSessionStore.reloadIfChanged(target);
    assert.equal(reloaded.reloaded, true);
    assert.ok(target.demo);
    assert.equal(target.demo.username, 'Demo');
  });

  test('ecosystem.site.json defines site and tracker ingest processes', () => {
    const eco = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'ecosystem.site.json'), 'utf8'));
    const names = eco.apps.map((a) => a.name);
    assert.ok(names.includes('deng-tool-site'));
    assert.ok(names.includes('deng-tracker-ingest'));
    const site = eco.apps.find((a) => a.name === 'deng-tool-site');
    const ingest = eco.apps.find((a) => a.name === 'deng-tracker-ingest');
    assert.equal(site.env.TOOL_SITE_PORT, '8791');
    assert.equal(site.env.TRACKER_WEB_MODE, '1');
    assert.equal(site.env.SKIP_TRACKER_UPLOAD_ROUTES, '1');
    assert.equal(ingest.env.TRACKER_INGEST_PORT, '8792');
  });

  test('public branding constants remain aio / DENG All In One', () => {
    assert.equal(PUBLIC_BRAND_NAME, 'DENG All In One');
    assert.equal(CANONICAL_PUBLIC_HOST, 'aio.deng.my.id');
  });

  test('permanent loader URL unchanged in public docs/tests', () => {
    const migration = fs.readFileSync(
      path.join(__dirname, 'fishit_fish_it_loader_migration.test.js'),
      'utf8',
    );
    assert.match(migration, /raw\.githubusercontent\.com\/dengjiangbin\/fish-it\/main\/tracker\.lua/);
    assert.match(migration, /loadstring\(game:HttpGet\(/);
  });
});
