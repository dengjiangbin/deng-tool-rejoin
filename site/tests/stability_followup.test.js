'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

process.env.NODE_ENV = 'test';

const { FileSessionStore, getSessionStoreMetrics } = require('../src/sessionStore');
const { getDiskFreeStatus, isDriveUsedForWrites } = require('../src/diskMonitor');
const { buildStabilityStatus } = require('../src/stabilityStatus');
const { trackerUploadKey } = require('../src/trackerUploadRateLimit');
const { isTrackerUploadPath } = require('../src/trackerUploadPaths');

describe('stability follow-up', () => {
  test('tracker upload path matcher includes all Roblox POST endpoints', () => {
    assert.equal(isTrackerUploadPath('POST', '/api/fishit-tracker/update-backpack'), true);
    assert.equal(isTrackerUploadPath('POST', '/api/tracker/update-catalog'), true);
    assert.equal(isTrackerUploadPath('GET', '/api/fishit-tracker/update-backpack'), false);
  });

  test('tracker upload rate key prefers userId then username', () => {
    const req = { body: { userId: 12345, username: 'Tester' }, headers: {}, ip: '1.2.3.4' };
    assert.equal(trackerUploadKey(req), 'uid:12345');
    const req2 = { body: { username: 'Tester' }, headers: {}, ip: '1.2.3.4' };
    assert.equal(trackerUploadKey(req2), 'user:tester');
  });

  test('anonymous session cleanup does not remove authenticated session', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'deng-session-anon-'));
    const store = new FileSessionStore({ dir, ttlMs: 7 * 24 * 60 * 60 * 1000 });
    store.anonymousTtlMs = 1000;

    await new Promise((resolve, reject) => {
      store.set('auth-user', {
        cookie: { expires: new Date(Date.now() + 3600_000) },
        user: { id: 'u1', discord_user_id: '123' },
      }, (err) => (err ? reject(err) : resolve()));
    });
    await new Promise((resolve, reject) => {
      store.set('anon-user', {
        cookie: { expires: new Date(Date.now() + 3600_000) },
        csrfToken: 'abc',
      }, (err) => (err ? reject(err) : resolve()));
    });

    const authFile = store._file('auth-user');
    const anonFile = store._file('anon-user');
    const old = Date.now() - 5000;
    fs.utimesSync(authFile, old / 1000, old / 1000);
    fs.utimesSync(anonFile, old / 1000, old / 1000);

    await store._runMaintenanceBatched();
    assert.equal(fs.existsSync(authFile), true);
    assert.equal(fs.existsSync(anonFile), false);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  test('disk monitor reports drive levels', () => {
    const disk = getDiskFreeStatus();
    assert.ok(Array.isArray(disk.drives));
    assert.ok(disk.drives.length >= 1);
    const cDrive = disk.drives.find((d) => d.drive === 'C:');
    if (cDrive) assert.ok(['ok', 'warning', 'critical'].includes(cDrive.level));
    assert.equal(isDriveUsedForWrites('D'), false);
  });

  test('stability status includes route and session metrics', () => {
    process.env.TOOL_SITE_SESSION_DIR = path.join(os.tmpdir(), 'deng-stability-metrics');
    const status = buildStabilityStatus();
    assert.equal(status.status, 'ok');
    assert.ok(status.trackerRoute);
    assert.ok(status.sessions.browser);
    assert.ok(status.disk);
    assert.ok(status.process.eventLoop);
  });

  test('cloudflared reference ingress validates', () => {
    const configPath = path.join(__dirname, '..', '..', 'config', 'cloudflared-ingress.reference.yml');
    const yaml = fs.readFileSync(configPath, 'utf8');
    assert.match(yaml, /8792/);
    assert.match(yaml, /8791/);
    assert.match(yaml, /3099/);
    assert.match(yaml, /aio\.deng\.my\.id/);
    assert.match(yaml, /\^\/api\/fishit-tracker\/\.\*/);
  });

  test('ecosystem includes web and ingest processes with proxy fallback', () => {
    const eco = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'ecosystem.site.json'), 'utf8'));
    const site = eco.apps.find((a) => a.name === 'deng-tool-site');
    assert.equal(site.env.TRACKER_UPLOAD_PROXY, '1');
    assert.equal(site.env.SKIP_TRACKER_UPLOAD_ROUTES, '1');
    assert.ok(eco.apps.find((a) => a.name === 'deng-tracker-ingest'));
  });
});
