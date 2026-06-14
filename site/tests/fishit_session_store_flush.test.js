'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fishit-session-flush-'));
const storePath = path.join(tmpDir, 'fishit_live_sessions.json');
process.env.FISHIT_LIVE_SESSIONS_PATH = storePath;
process.env.FISHIT_SESSION_SYNC_SAVE = '1';

const sessionStore = require('../src/fishitSessionStore');

describe('fishit session store debounced flush', () => {
  beforeEach(() => {
    sessionStore._reset();
  });

  test('saveSession writes compact JSON without re-reading full file each time', () => {
    const t0 = Date.now();
    for (let i = 0; i < 20; i += 1) {
      sessionStore.saveSession(`user${i}`, {
        username: `user${i}`,
        userId: i,
        items: [],
        isOnline: true,
        lastSeenAt: new Date().toISOString(),
      }, {});
    }
    const elapsed = Date.now() - t0;
    assert.ok(fs.existsSync(storePath));
    const raw = fs.readFileSync(storePath, 'utf8');
    assert.doesNotMatch(raw, /\n  "/);
    const parsed = JSON.parse(raw);
    assert.equal(Object.keys(parsed.sessions).length, 20);
    assert.ok(elapsed < 3000, `20 saves took ${elapsed}ms — likely still doing full sync reads`);
  });

  test('reloadIfChanged merges disk updates after flush', () => {
    sessionStore.saveSession('demo', {
      username: 'demo',
      userId: 1,
      items: [{ name: 'Fish', amount: 1 }],
      isOnline: true,
      lastSeenAt: new Date().toISOString(),
    }, {});
    const live = {};
    sessionStore.loadIntoLiveTrackDB(live);
    sessionStore.saveSession('demo', {
      ...live.demo,
      fishItemCount: 9,
      lastSeenAt: new Date().toISOString(),
    }, live);
    sessionStore._invalidateReloadCursorForTests();
    const reloaded = sessionStore.reloadIfChanged(live);
    assert.equal(reloaded.reloaded, true);
    assert.equal(live.demo.fishItemCount, 9);
  });
});
