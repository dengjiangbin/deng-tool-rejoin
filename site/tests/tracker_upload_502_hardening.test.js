'use strict';

const { describe, test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';
process.env.INVENTORY_ACCOUNTS_MEMORY = '1';

const sessionStore = require('../src/fishitSessionStore');
const trackerRoutes = require('../src/fishitTrackerRoutes');
const { finishTrackerUploadResponse } = require('../src/trackerUploadResponse');
const { MINIMUM_TRACKER_BUILD } = require('../src/fishitTrackerBuild');
const { RAW_TRACKER_LUA, testIfRawTracker } = require('./helpers/trackerRawSource');

const liveTrackDB = trackerRoutes.liveTrackDB;

function makeApp() {
  const app = express();
  app.use(express.json({ limit: '512kb' }));
  app.use((req, _res, next) => {
    req.inventoryOwnerDiscordId = '123456789012345678';
    next();
  });
  app.use(trackerRoutes);
  return app;
}

describe('tracker upload 502 hardening + durable latest upload', () => {
  let tmpStore;

  before(() => {
    tmpStore = path.join(os.tmpdir(), `fishit-502-${Date.now()}.json`);
    process.env.FISHIT_LIVE_SESSIONS_PATH = tmpStore;
    process.env.FISHIT_SESSION_SYNC_SAVE = '0';
    sessionStore._reset();
  });

  after(() => {
    sessionStore._reset();
    try { fs.unlinkSync(tmpStore); } catch (_) { /* ignore */ }
    delete process.env.FISHIT_LIVE_SESSIONS_PATH;
    delete process.env.FISHIT_SESSION_SYNC_SAVE;
    delete process.env.TRACKER_INGEST_MODE;
  });

  test('persistSessionHeartbeat writes session readable after disk reload', async () => {
    const flushStore = path.join(os.tmpdir(), `fishit-priority-${Date.now()}.json`);
    process.env.FISHIT_LIVE_SESSIONS_PATH = flushStore;
    sessionStore._reset();

    const key = 'priorityflush';
    liveTrackDB[key] = {
      username: 'PriorityFlush',
      userId: 1,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      isOnline: true,
      playerStats: {
        coins: 10,
        totalCaught: 5,
        rarestFishChance: '1/50',
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
      lastStatsUploadAt: new Date().toISOString(),
    };
    trackerRoutes.persistSessionHeartbeat(key);

    sessionStore._invalidateReloadCursorForTests();
    const reloadDb = {};
    const reload = sessionStore.reloadIfChanged(reloadDb);
    assert.equal(reload.reloaded, true);
    assert.equal(reloadDb[key].username, 'PriorityFlush');

    sessionStore._reset();
    try { fs.unlinkSync(flushStore); } catch (_) { /* ignore */ }
  });

  test('upload while no tracker page open is readable after disk reload', async () => {
    const key = 'nofrontend';
    const username = 'NoFrontend';
    const app = makeApp();
    const body = {
      type: 'inventory_snapshot',
      username,
      userId: 44,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      clientOrigin: 'roblox_tracker',
      intervalSeconds: 60,
      isOnline: true,
      playerStats: {
        coins: 777,
        totalCaught: 888,
        rarestFishChance: '1/300',
        source: 'leaderstats',
        build: MINIMUM_TRACKER_BUILD,
      },
      fishItems: [{ itemId: '99', name: 'Offline Fish', quantity: 2, source: 'playerdata_gameitemdb' }],
      stoneItems: [],
      totemItems: [],
    };

    const uploadRes = await request(app).post('/api/fishit-tracker/update-backpack').send(body);
    assert.ok([200, 202].includes(uploadRes.status));
    await sessionStore.flushToDiskAsync({ priority: true });

    const freshDb = {};
    sessionStore._invalidateReloadCursorForTests();
    sessionStore.reloadIfChanged(freshDb);
    assert.ok(freshDb[key], 'session must exist on disk without frontend polling');
    assert.equal(freshDb[key].playerStats.coins, 777);

    const readRes = await request(makeApp()).get(`/api/tracker/get-backpack/${key}`).expect(200);
    assert.ok(readRes.body.inventoryLastSuccessAt || readRes.body.statusLastSuccessAt);
  });

  test('finishTrackerUploadResponse keeps 202 as success contract', () => {
    const key = 'contract202';
    const now = new Date().toISOString();
    liveTrackDB[key] = {
      username: 'Contract202',
      userId: 2,
      trackerBuild: MINIMUM_TRACKER_BUILD,
      leaderstatsUploadOk: true,
      lastStatsUploadAt: now,
      playerStats: {
        coins: 1,
        totalCaught: 2,
        rarestFishChance: '1/10',
        source: 'leaderstats',
        statsAt: now,
        build: MINIMUM_TRACKER_BUILD,
      },
    };
    const req = { headers: {}, trackerDeferEnrichment: true };
    const res = {
      statusCode: 200,
      body: null,
      _events: {},
      once(evt, fn) { this._events[evt] = fn; return this; },
      status(code) { this.statusCode = code; return this; },
      set() { return this; },
      json(payload) { this.body = payload; if (this._events.finish) this._events.finish(); return this; },
    };
    process.env.TRACKER_INGEST_MODE = '1';
    finishTrackerUploadResponse(req, res, { ok: true, status: 'success', acceptedCount: 1, lastSeenAt: now, serverTime: now }, key);
    assert.equal(res.statusCode, 202);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.accepted, true);
  });

  testIfRawTracker('Lua treats 502/503/504 as failure with transient backoff', () => {
    const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
    const fn = lua.match(/function HttpDash\.uploadOkFromResult\([\s\S]*?^end/m);
    assert.ok(fn);
    assert.doesNotMatch(fn[0], /502.*return true/);
    assert.match(lua, /TRANSIENT_UPLOAD_BACKOFF/);
    assert.match(lua, /code == 502 or code == 503 or code == 504/);
  });
});
