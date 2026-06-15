'use strict';
/**
 * Tests for DENG Tool: Rejoin APK backend monitor routes (monitorRoutes.js).
 *
 * These tests inject a tiny in-memory mock for ./db so we can exercise
 * route logic without a real Supabase instance. The mock supports just
 * the subset of the Supabase JS client API that monitorRoutes uses:
 *   from().select().eq()/maybeSingle()/limit()/order()/insert()/upsert()/update()
 */

const { describe, test, before, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// ── Env required by app.js ──────────────────────────────────────────────────
process.env.TOOL_SITE_COOKIE_SECRET = 'monitor-test-cookie-secret-long-enough-yes';
process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.TOOL_SITE_PUBLIC_URL = 'http://localhost:8791';
process.env.DISCORD_CLIENT_ID = 'x';
process.env.DISCORD_CLIENT_SECRET = 'x';
process.env.DISCORD_REDIRECT_URI = 'http://localhost:8791/auth/discord/callback';
process.env.TOOL_SITE_STATE_SECRET = 'monitor-test-state-secret-long-enough-yes';

const fakeAxios = {
  async post() {
    return { data: { access_token: 'discord-access-token' } };
  },
  async get() {
    return {
      data: {
        id: 'discord-user-1',
        username: 'DiscordTester',
        avatar: null,
        email: null,
      },
    };
  },
};

// ── Tiny Supabase mock with monitor-table support ───────────────────────────
function makeMemoryDb() {
  return {
    site_users: [],
    monitor_devices: [],
    monitor_package_states: [],
    monitor_snapshots: [],
    monitor_settings: [],
    monitor_bridge_tokens: [],
    monitor_pairing_codes: [],
    monitor_app_sessions: [],
    // License-system tables used by /api/monitor/bridge/issue-from-license.
    license_keys: [],
    device_bindings: [],
  };
}

let mem = makeMemoryDb();

class Q {
  constructor(table) {
    this.table = table;
    this.filters = [];
    this.orderSpec = null;
    this.limitCount = null;
    this.action = 'select';
    this.payload = null;
    this.upsertOnConflict = null;
    this.selectColumns = '*';
  }
  _rows() { return mem[this.table] || (mem[this.table] = []); }
  _matches(row) {
    return this.filters.every((f) => {
      if (f.op === 'in') return Array.isArray(f.value) && f.value.includes(row[f.field]);
      return row[f.field] === f.value;
    });
  }

  select(cols) { if (cols) this.selectColumns = cols; return this; }
  insert(payload) { this.action = 'insert'; this.payload = Array.isArray(payload) ? payload : [payload]; return this; }
  update(payload) { this.action = 'update'; this.payload = payload; return this; }
  upsert(payload, opts = {}) { this.action = 'upsert'; this.payload = Array.isArray(payload) ? payload : [payload]; this.upsertOnConflict = opts.onConflict || null; return this; }
  delete() { this.action = 'delete'; return this; }
  eq(field, value) { this.filters.push({ field, value, op: 'eq' }); return this; }
  in(field, values) { this.filters.push({ field, value: values, op: 'in' }); return this; }
  order(field, spec = {}) { this.orderSpec = { field, ascending: spec.ascending !== false }; return this; }
  limit(n) { this.limitCount = n; return this; }

  async maybeSingle() { const { data } = await this._run(); return { data: data[0] || null, error: null }; }
  async single() { const { data } = await this._run(); return data[0] ? { data: data[0], error: null } : { data: null, error: { message: 'no rows' } }; }
  then(resolve, reject) { return this._run().then(resolve, reject); }

  async _run() {
    const rows = this._rows();
    const now = new Date().toISOString();
    if (this.action === 'insert') {
      const inserted = this.payload.map((row) => {
        const next = { id: row.id || crypto.randomUUID(), created_at: now, updated_at: now, ...row };
        rows.push(next);
        return next;
      });
      return { data: inserted, error: null };
    }
    if (this.action === 'upsert') {
      const keys = (this.upsertOnConflict || '').split(',').map((s) => s.trim()).filter(Boolean);
      const result = [];
      for (const row of this.payload) {
        let existing = null;
        if (keys.length) {
          existing = rows.find((r) => keys.every((k) => r[k] === row[k]));
        }
        if (existing) {
          Object.assign(existing, row, { updated_at: now });
          result.push(existing);
        } else {
          const next = { id: row.id || crypto.randomUUID(), created_at: now, updated_at: now, ...row };
          rows.push(next);
          result.push(next);
        }
      }
      return { data: result, error: null };
    }
    if (this.action === 'update') {
      const updated = [];
      for (const row of rows) {
        if (this._matches(row)) { Object.assign(row, this.payload, { updated_at: now }); updated.push(row); }
      }
      return { data: updated, error: null };
    }
    let result = rows.filter((r) => this._matches(r));
    if (this.orderSpec) {
      const { field, ascending } = this.orderSpec;
      result = [...result].sort((a, b) => {
        const av = a[field] || ''; const bv = b[field] || '';
        if (av === bv) return 0;
        return av > bv ? (ascending ? 1 : -1) : (ascending ? -1 : 1);
      });
    }
    if (this.limitCount !== null) result = result.slice(0, this.limitCount);
    return { data: result, error: null };
  }
}

const mockSupabase = { from(table) { return new Q(table); } };

// Inject the mock for ./db BEFORE app code requires it.
const dbPath = path.join(__dirname, '..', 'src', 'db.js');
require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: mockSupabase };
require.cache[require.resolve('axios')] = {
  id: require.resolve('axios'),
  filename: require.resolve('axios'),
  loaded: true,
  exports: fakeAxios,
};

// Now safe to load the app.
const request = require('supertest');
let app;

before(() => {
  app = require('../src/app');
});

beforeEach(() => {
  mem = makeMemoryDb();
});

// ── Helpers ─────────────────────────────────────────────────────────────────
async function login(agent) {
  const start = await agent.get('/auth/discord');
  assert.equal(start.status, 302);
  const state = new URL(start.headers.location).searchParams.get('state');
  const res = await agent.get(`/auth/discord/callback?code=ok&state=${state}`);
  assert.equal(res.status, 302);
  assert.equal(res.headers.location, '/tracker');
}

function sha256(s) { return crypto.createHash('sha256').update(String(s)).digest('hex'); }

function seedDevice(owner = 'discord-user-1') {
  const deviceId = crypto.randomUUID();
  mem.monitor_devices.push({
    id: deviceId,
    owner_discord_user_id: owner,
    device_label: 'Test Phone',
    device_fingerprint_hash: sha256('test-fp'),
    tool_version: '1.0.0',
    channel: 'stable',
    status_connected: true,
    last_seen_at: new Date().toISOString(),
  });
  return deviceId;
}

function seedBridgeToken(deviceId) {
  const token = 'bridge-test-token-' + crypto.randomBytes(8).toString('hex');
  mem.monitor_bridge_tokens.push({
    id: crypto.randomUUID(),
    monitor_device_id: deviceId,
    token_hash: sha256(token),
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  });
  return token;
}

function seedAppSession(owner) {
  const token = 'app-test-token-' + crypto.randomBytes(8).toString('hex');
  mem.monitor_app_sessions.push({
    id: crypto.randomUUID(),
    owner_discord_user_id: owner,
    token_hash: sha256(token),
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  });
  return token;
}

// ── Tests ───────────────────────────────────────────────────────────────────

describe('monitor bridge auth', () => {
  test('rejects push without Bearer token', async () => {
    const res = await request(app)
      .post('/api/monitor/bridge/push')
      .send({ schema: 1, packages: [] });
    assert.equal(res.status, 401);
    assert.equal(res.body.error, 'unauthorized');
  });

  test('rejects push with invalid token', async () => {
    const res = await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', 'Bearer not-a-real-token')
      .send({ schema: 1, packages: [] });
    assert.equal(res.status, 401);
  });

  test('rejects push with revoked token', async () => {
    const deviceId = seedDevice();
    const token = seedBridgeToken(deviceId);
    mem.monitor_bridge_tokens[0].revoked_at = new Date().toISOString();
    const res = await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({ schema: 1, packages: [] });
    assert.equal(res.status, 401);
  });

  test('accepts push with valid token and stores package state', async () => {
    const deviceId = seedDevice('disc-1');
    const token = seedBridgeToken(deviceId);

    const res = await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({
        schema: 1,
        tool_version: '1.0.0',
        channel: 'stable',
        captured_at: Date.now() / 1000,
        packages: [
          { package: 'com.litec.client', username: 'deng1629', state: 'Online', ram_mb: 642, runtime_seconds: 8073, restart_count: 2, private_url_configured: true },
        ],
      });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.accepted, 1);
    assert.equal(mem.monitor_package_states.length, 1);
    const stored = mem.monitor_package_states[0];
    assert.equal(stored.package_name, 'com.litec.client');
    assert.equal(stored.ram_mb, 642);
    assert.equal(stored.private_url_configured, true);
  });

  // v1.0.2 — APK settings reach Termux via the /push echo so the user can
  // change snapshot interval without relaunching Termux.
  test('push response echoes current monitor_settings so bridge can react live', async () => {
    const deviceId = seedDevice('disc-echo');
    const token = seedBridgeToken(deviceId);
    mem.monitor_settings.push({
      monitor_device_id: deviceId,
      snapshot_interval_seconds: 60,
      monitor_enabled: true,
      app_refresh_interval_seconds: 5,
    });

    const res = await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({
        schema: 1,
        tool_version: '1.0.0',
        channel: 'stable',
        packages: [],
      });
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
    assert.ok(res.body.settings, 'push response must echo settings');
    assert.equal(res.body.settings.snapshot_interval_seconds, 60);
    assert.equal(res.body.settings.monitor_enabled, true);
    assert.equal(res.body.settings.app_refresh_interval_seconds, 5);
  });

  test('push response settings field is null when no settings row exists', async () => {
    const deviceId = seedDevice('disc-no-settings');
    const token = seedBridgeToken(deviceId);
    const res = await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({ schema: 1, tool_version: '1.0.0', channel: 'stable', packages: [] });
    assert.equal(res.status, 200);
    // settings may be null (no row); the bridge tolerates either shape.
    assert.ok(res.body.settings === null || res.body.settings === undefined);
  });
});

describe('monitor bridge payload validation', () => {
  test('rejects unsupported schema', async () => {
    const deviceId = seedDevice();
    const token = seedBridgeToken(deviceId);
    const res = await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({ schema: 999, packages: [] });
    assert.equal(res.status, 400);
    assert.equal(res.body.message, 'unsupported_schema');
  });

  test('rejects oversized snapshot above v1.0.3 5MB limit', async () => {
    // v1.0.3: server cap raised from 1.5MB → 5MB to accommodate full-DPI
    // Samsung cloud-phone PNGs. Allocate 6MB to stay above the new cap.
    const deviceId = seedDevice();
    const token = seedBridgeToken(deviceId);
    const big = Buffer.alloc(6 * 1024 * 1024, 0xff);
    const res = await request(app)
      .post('/api/monitor/bridge/snapshot')
      .set('Authorization', `Bearer ${token}`)
      .set('Content-Type', 'image/png')
      .send(big);
    assert.ok(res.status === 413 || res.status === 400, `unexpected status ${res.status}`);
  });

  test('clamps unsafe per-package fields and never returns license_key', async () => {
    const deviceId = seedDevice();
    const token = seedBridgeToken(deviceId);
    await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({
        schema: 1,
        tool_version: '1.0.0',
        packages: [{
          package: 'com.foo.bar',
          state: 'Online',
          ram_mb: 9999999,           // should clamp to 65536
          runtime_seconds: -10,       // should clamp to 0
          restart_count: 'not-a-num', // should coerce to 0
          // attempt to smuggle banned fields:
          license_key: 'DENG-XXXX-YYYY-ZZZZ-WWWW',
          private_url: 'https://private/server',
          hwid: 'raw-hwid',
        }],
      });
    const stored = mem.monitor_package_states[0];
    assert.equal(stored.ram_mb, 65536);
    assert.equal(stored.runtime_seconds, 0);
    assert.equal(stored.restart_count, 0);
    assert.equal(stored.license_key, undefined);
    assert.equal(stored.private_url, undefined);
    assert.equal(stored.hwid, undefined);
  });
});

describe('app session auth + ownership isolation', () => {
  test('listDevices rejects without token', async () => {
    const res = await request(app).get('/api/monitor/devices');
    assert.equal(res.status, 401);
  });

  test('listDevices only returns devices for the authenticated owner', async () => {
    const myDevice = seedDevice('disc-me');
    seedDevice('disc-other');
    const myToken = seedAppSession('disc-me');
    const res = await request(app)
      .get('/api/monitor/devices')
      .set('Authorization', `Bearer ${myToken}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.devices.length, 1);
    assert.equal(res.body.devices[0].id, myDevice);
  });

  test('listDevices package_summary: 1 device + 8 dead packages → TOTAL 8 / ONLINE 0 / DEAD 8', async () => {
    const owner = 'disc-pkg';
    const deviceId = seedDevice(owner);
    const token = seedBridgeToken(deviceId);
    const deadPkgs = Array.from({ length: 8 }, (_, i) => ({
      package: `com.moons.lite${String.fromCharCode(99 + i)}`,
      state: 'Dead',
      ram_mb: 0,
    }));
    await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({ schema: 1, tool_version: '1.0.8', channel: 'stable', packages: deadPkgs });
    const appToken = seedAppSession(owner);
    const res = await request(app)
      .get('/api/monitor/devices')
      .set('Authorization', `Bearer ${appToken}`);
    assert.equal(res.status, 200);
    const ps = res.body.package_summary;
    assert.equal(ps.total, 8);
    assert.equal(ps.online, 0);
    assert.equal(ps.dead, 8);
    // Device count must not replace package count on the headline cards.
    assert.equal(res.body.devices.length, 1);
    assert.equal(res.body.devices[0].package_summary.total, 8);
    assert.equal(res.body.devices[0].package_summary.dead, 8);
  });

  test('cannot read another user device status', async () => {
    const myToken = seedAppSession('disc-me');
    const othersDevice = seedDevice('disc-other');
    const res = await request(app)
      .get(`/api/monitor/devices/${othersDevice}/status`)
      .set('Authorization', `Bearer ${myToken}`);
    assert.equal(res.status, 404);
  });

  test('status response v1.0.3 reports last_snapshot_captured_at when a snapshot exists', async () => {
    // v1.0.3: backend echoes the newest snapshot's captured_at to the
    // APK so SnapshotScreen can render "Waiting…" vs "Captured: …"
    // honestly instead of the v1.0.2 "No snapshot yet." silent default.
    const owner = 'disc-me';
    const deviceId = seedDevice(owner);
    const myToken = seedAppSession(owner);
    const capturedAt = new Date(Date.now() - 12_000).toISOString();
    mem.monitor_snapshots.push({
      id: crypto.randomUUID(),
      monitor_device_id: deviceId,
      mime_type: 'image/png',
      image_data: Buffer.from([0x89, 0x50, 0x4e, 0x47]),
      size_bytes: 4,
      captured_at: capturedAt,
    });
    const res = await request(app)
      .get(`/api/monitor/devices/${deviceId}/status`)
      .set('Authorization', `Bearer ${myToken}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.device.last_snapshot_captured_at, capturedAt);
    assert.ok(
      typeof res.body.device.last_snapshot_age_seconds === 'number' &&
        res.body.device.last_snapshot_age_seconds >= 0,
      'last_snapshot_age_seconds should be a non-negative number',
    );
  });

  test('status response v1.0.3 reports null snapshot fields when no snapshot exists', async () => {
    const owner = 'disc-me';
    const deviceId = seedDevice(owner);
    const myToken = seedAppSession(owner);
    const res = await request(app)
      .get(`/api/monitor/devices/${deviceId}/status`)
      .set('Authorization', `Bearer ${myToken}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.device.last_snapshot_captured_at, null);
    assert.equal(res.body.device.last_snapshot_age_seconds, null);
    // Default monitor_settings still echoes 30s interval per v1.0.2 contract.
    assert.equal(res.body.settings.snapshot_interval_seconds, 30);
  });

  test('settings update rejects invalid snapshot interval', async () => {
    const myDevice = seedDevice('disc-me');
    mem.monitor_settings.push({ monitor_device_id: myDevice });
    const myToken = seedAppSession('disc-me');
    const res = await request(app)
      .patch(`/api/monitor/devices/${myDevice}/settings`)
      .set('Authorization', `Bearer ${myToken}`)
      .send({ snapshot_interval_seconds: 7 });
    assert.equal(res.status, 400);
  });

  test('settings update accepts allowed interval', async () => {
    const myDevice = seedDevice('disc-me');
    mem.monitor_settings.push({ monitor_device_id: myDevice });
    const myToken = seedAppSession('disc-me');
    const res = await request(app)
      .patch(`/api/monitor/devices/${myDevice}/settings`)
      .set('Authorization', `Bearer ${myToken}`)
      .send({ snapshot_interval_seconds: 60 });
    assert.equal(res.status, 200);
    const row = mem.monitor_settings.find((r) => r.monitor_device_id === myDevice);
    assert.equal(row.snapshot_interval_seconds, 60);
  });

  test('settings update preserves snapshot interval Off as 0', async () => {
    const myDevice = seedDevice('disc-off');
    mem.monitor_settings.push({ monitor_device_id: myDevice, snapshot_interval_seconds: 30 });
    const myToken = seedAppSession('disc-off');
    const save = await request(app)
      .patch(`/api/monitor/devices/${myDevice}/settings`)
      .set('Authorization', `Bearer ${myToken}`)
      .send({ snapshot_interval_seconds: 0 });
    assert.equal(save.status, 200);
    assert.equal(save.body.settings.snapshot_interval_seconds, 0);

    const status = await request(app)
      .get(`/api/monitor/devices/${myDevice}/status`)
      .set('Authorization', `Bearer ${myToken}`);
    assert.equal(status.status, 200);
    assert.equal(status.body.settings.snapshot_interval_seconds, 0);
  });

  test('settings update persists non-30 refresh interval and echoes saved settings', async () => {
    const myDevice = seedDevice('disc-me');
    mem.monitor_settings.push({
      monitor_device_id: myDevice,
      snapshot_interval_seconds: 30,
      monitor_enabled: true,
      app_refresh_interval_seconds: 30,
    });
    const myToken = seedAppSession('disc-me');
    const save = await request(app)
      .patch(`/api/monitor/devices/${myDevice}/settings`)
      .set('Authorization', `Bearer ${myToken}`)
      .send({ app_refresh_interval_seconds: 10, snapshot_interval_seconds: 60 });
    assert.equal(save.status, 200);
    assert.equal(save.body.settings.app_refresh_interval_seconds, 10);
    assert.equal(save.body.settings.snapshot_interval_seconds, 60);

    const status = await request(app)
      .get(`/api/monitor/devices/${myDevice}/status`)
      .set('Authorization', `Bearer ${myToken}`);
    assert.equal(status.status, 200);
    assert.equal(status.body.settings.app_refresh_interval_seconds, 10);
    assert.equal(status.body.device.monitor_interval_seconds, 10);

    const list = await request(app)
      .get('/api/monitor/devices')
      .set('Authorization', `Bearer ${myToken}`);
    assert.equal(list.status, 200);
    assert.equal(list.body.devices[0].monitor_interval_seconds, 10);
  });
});

describe('pairing flow', () => {
  test('redeem with invalid code returns 401', async () => {
    const res = await request(app)
      .post('/api/monitor/pairing/redeem')
      .send({ code: 'ABCDEFGH' });
    assert.equal(res.status, 401);
  });

  test('expired code is rejected', async () => {
    const code = 'EXPIRED1';
    mem.monitor_pairing_codes.push({
      id: crypto.randomUUID(),
      code_hash: sha256(code),
      owner_discord_user_id: 'disc-me',
      expires_at: new Date(Date.now() - 1000).toISOString(),
    });
    const res = await request(app)
      .post('/api/monitor/pairing/redeem')
      .send({ code });
    assert.equal(res.status, 401);
    assert.equal(res.body.message, 'code_expired');
  });

  test('valid code issues app session token', async () => {
    const code = 'VALID123';
    mem.monitor_pairing_codes.push({
      id: crypto.randomUUID(),
      code_hash: sha256(code),
      owner_discord_user_id: 'disc-me',
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    });
    const res = await request(app)
      .post('/api/monitor/pairing/redeem')
      .send({ code, device_name: 'Pixel 6' });
    assert.equal(res.status, 200);
    assert.ok(res.body.app_session_token);
    assert.equal(res.body.owner.discord_user_id, 'disc-me');
    assert.equal(mem.monitor_pairing_codes[0].used_at !== undefined, true);
  });
});

describe('APK download page', () => {
  test('redirects logged-out visitors from /download to login with return path', async () => {
    const res = await request(app).get('/download');
    assert.equal(res.status, 302);
    assert.match(res.headers.location, /^\/login\?return=%2Fdownload$/);
  });

  test('renders /download with new product name and primary CTA when signed in', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/download');
    assert.equal(res.status, 200);
    assert.match(res.text, /DENG All In One/);
    assert.match(res.text, /Download DENG All In One APK/);
    assert.match(res.text, /Install Instructions/);
  });

  test('/download does NOT show legacy "DENG Monitor" naming', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/download');
    assert.equal(res.status, 200);
    assert.doesNotMatch(res.text, /DENG Monitor/);
    // Must not imply the APK version equals the Rejoin package version.
    assert.doesNotMatch(res.text, /Package Version/);
  });

  test('returns 404 for unknown APK filenames', async () => {
    const res = await request(app).get('/downloads/something-evil.apk');
    assert.equal(res.status, 404);
  });

  test('rejects traversal attempts in APK filename', async () => {
    const res = await request(app).get('/downloads/..%2Fserver.js');
    assert.equal(res.status, 404);
  });

  test('canonical latest alias either serves APK (when published) or 404s cleanly', async () => {
    const res = await request(app)
      .get('/downloads/deng-all-in-one-apk-latest.apk')
      .redirects(0);
    // When an APK is published, the alias 302-redirects to the versioned filename.
    // When no APK is published, the route returns 404 with a friendly message.
    if (res.status === 302) {
      assert.match(
        String(res.headers.location || ''),
        /^\/downloads\/deng-all-in-one-apk-v[\d.]+\.apk$/,
        'latest alias must redirect to a versioned filename when an APK is published',
      );
    } else {
      assert.equal(res.status, 404);
      assert.match(res.text, /APK not available yet/);
    }
  });

  test('HEAD latest APK follows to the file without incrementing download count', async () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dl-head-'));
    const oldDownloadStatsPath = process.env.DOWNLOAD_STATS_PATH;
    const oldApkStatsPath = process.env.APK_DOWNLOAD_STATS_PATH;
    const oldAndroidStatsPath = process.env.ANDROID_DOWNLOAD_STATS_PATH;
    process.env.DOWNLOAD_STATS_PATH = path.join(tmpDir, 'stats.json');
    delete process.env.APK_DOWNLOAD_STATS_PATH;
    delete process.env.ANDROID_DOWNLOAD_STATS_PATH;
    try {
      const ds = require('../src/downloadStats');
      const beforeCount = ds.getApkStats().latest?.downloads || 0;
      const res = await request(app)
        .head('/downloads/deng-all-in-one-apk-latest.apk')
        .redirects(1);
      if (res.status === 200) {
        assert.match(
          String(res.headers['content-type'] || ''),
          /application\/(vnd\.android\.package-archive|octet-stream)/,
        );
      } else {
        assert.equal(res.status, 404);
      }
      assert.equal(ds.getApkStats().latest?.downloads || 0, beforeCount);
    } finally {
      if (oldDownloadStatsPath === undefined) delete process.env.DOWNLOAD_STATS_PATH;
      else process.env.DOWNLOAD_STATS_PATH = oldDownloadStatsPath;
      if (oldApkStatsPath === undefined) delete process.env.APK_DOWNLOAD_STATS_PATH;
      else process.env.APK_DOWNLOAD_STATS_PATH = oldApkStatsPath;
      if (oldAndroidStatsPath === undefined) delete process.env.ANDROID_DOWNLOAD_STATS_PATH;
      else process.env.ANDROID_DOWNLOAD_STATS_PATH = oldAndroidStatsPath;
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  test('legacy latest alias permanently redirects to new latest alias', async () => {
    const res = await request(app)
      .get('/downloads/deng-monitor-latest.apk')
      .redirects(0);
    assert.equal(res.status, 301);
    assert.equal(res.headers.location, '/downloads/deng-all-in-one-apk-latest.apk');
  });

  test('legacy rejoin latest alias permanently redirects to canonical latest', async () => {
    const res = await request(app)
      .get('/downloads/deng-tool-rejoin-apk-latest.apk')
      .redirects(0);
    assert.equal(res.status, 301);
    assert.equal(res.headers.location, '/downloads/deng-all-in-one-apk-latest.apk');
  });

  test('legacy versioned filename redirects to deng-all-in-one pattern when missing on disk', async () => {
    const res = await request(app)
      .get('/downloads/deng-monitor-v1.0.0.apk')
      .redirects(0);
    assert.equal(res.status, 301);
    assert.equal(res.headers.location, '/downloads/deng-all-in-one-apk-v1.0.0.apk');
  });

  test('legacy rejoin versioned filename redirects to deng-all-in-one pattern when missing on disk', async () => {
    const res = await request(app)
      .get('/downloads/deng-tool-rejoin-apk-v1.0.0.apk')
      .redirects(0);
    assert.equal(res.status, 301);
    assert.equal(res.headers.location, '/downloads/deng-all-in-one-apk-v1.0.0.apk');
  });

  test('canonical versioned filename serves real APK when published, 404 otherwise', async () => {
    const res = await request(app).get('/downloads/deng-all-in-one-apk-v1.0.0.apk');
    // Either the APK has been published (200 + APK content-type) or it's
    // missing and the route 404s cleanly. Both are valid security postures;
    // what must NEVER happen is leaking another file.
    if (res.status === 200) {
      assert.match(
        String(res.headers['content-type'] || ''),
        /application\/(vnd\.android\.package-archive|octet-stream)/,
      );
      assert.ok(Buffer.isBuffer(res.body) || typeof res.body === 'object');
    } else {
      assert.equal(res.status, 404);
    }
  });

  test('traversal attempt via legacy filename pattern is rejected', async () => {
    // Even if the legacy regex matches, the resolved-path defense must block.
    const res = await request(app).get('/downloads/deng-monitor-..%2F..%2Fpackage.json');
    assert.equal(res.status, 404);
  });

  test('/download shows the Pair Android App section', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/download');
    assert.equal(res.status, 200);
    assert.match(res.text, /Pair Android App/);
    assert.match(res.text, /id="pair-android-app"/);
    assert.match(
      res.text,
      /After installing DENG All In One, open the app and sign in with Discord/,
    );
  });

  test('/download logged-in shows the pair panel for authenticated users', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/download');
    assert.equal(res.status, 200);
    assert.match(res.text, /Pair Android App/);
    assert.doesNotMatch(res.text, /data-pair-panel-loggedout/);
  });

  test('/download does NOT instruct users to use the License page for pairing', async () => {
    const agent = request.agent(app);
    await login(agent);
    const res = await agent.get('/download');
    assert.equal(res.status, 200);
    // The Pair Android App section must point at the Download page (this page),
    // not at /license or "My License".
    assert.doesNotMatch(res.text, /pair[^<]{0,40}(?:License page|My License)/i);
  });

  test('download.ejs template hosts the copy-friendly pair code UI in the logged-in branch', async () => {
    // Source-level inspection: the EJS template must contain all the
    // hooks the client JS depends on. This avoids needing a real session.
    const fs = require('fs');
    const path = require('path');
    const tpl = fs.readFileSync(
      path.resolve(__dirname, '..', 'views', 'download.ejs'),
      'utf8',
    );
    // The logged-in branch must be present and gated on `user`.
    assert.match(tpl, /<%\s*if\s*\(\s*user\s*\)\s*\{\s*%>/);
    // It must include the Generate button, the read-only code field, and
    // a Copy Code button.
    assert.match(tpl, /data-pair-generate/);
    assert.match(tpl, /data-pair-code/);
    assert.match(tpl, /data-pair-copy/);
    assert.match(tpl, /readonly/);
    assert.match(tpl, />\s*Generate Pair Code\s*</);
    assert.match(tpl, />\s*Copy Code\s*</);
    assert.match(tpl, /Expires in 5 minutes/);
    // Mobile-friendly: the code input must have user-select:all so long-press
    // copies the whole code on touch devices.
    assert.match(tpl, /user-select:\s*all/);
    // The JS calls the existing pairing create endpoint.
    assert.match(tpl, /\/api\/monitor\/pairing\/create/);
  });

  test('pair-code create endpoint rejects unauthenticated requests (401/403/302)', async () => {
    const res = await request(app)
      .post('/api/monitor/pairing/create')
      .set('Content-Type', 'application/json')
      .send('{}');
    // requireLogin returns 401/403 for JSON clients and 302 for HTML
    // clients. Either is a valid "you are not logged in" response —
    // the only invalid outcome is success (200) without a session.
    assert.ok(
      [401, 403, 302].includes(res.status),
      `expected 401/403/302, got ${res.status}`,
    );
    assert.notEqual(res.status, 200);
  });
});

// ───────────────────────────────────────────────────────────────────────────
// Termux-safe bridge token issuance (no website session required)
// ───────────────────────────────────────────────────────────────────────────

const VALID_LICENSE_KEY = 'DENG-1A2B-3C4D-5E6F-7890';
const VALID_INSTALL_ID  = 'a'.repeat(32);
function installIdHashFor(id) { return sha256(id); }
function licenseKeyIdFor(key) { return sha256(String(key).toUpperCase()); }

function seedActiveLicense({
  owner = 'discord-owner-1',
  key = VALID_LICENSE_KEY,
  installId = VALID_INSTALL_ID,
  status = 'active',
  expiresAt = null,
  bindingActive = true,
} = {}) {
  const keyId = licenseKeyIdFor(key);
  mem.license_keys.push({
    id: keyId,
    prefix: 'DENG-1A2B',
    suffix: '7890',
    owner_discord_id: owner,
    status,
    plan: 'standard',
    expires_at: expiresAt,
    created_by: owner,
  });
  mem.device_bindings.push({
    key_id: keyId,
    install_id_hash: installIdHashFor(installId),
    device_label: '',
    device_model: '',
    is_active: bindingActive,
  });
  return { key, installId, installIdHash: installIdHashFor(installId), keyId, owner };
}

describe('POST /api/monitor/bridge/issue-from-license', () => {
  test('rejects bad license-key format', async () => {
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: 'not-a-key', install_id_hash: 'a'.repeat(64) });
    assert.equal(res.status, 400);
    assert.equal(res.body.message, 'invalid_license_key');
  });

  test('rejects bad install-id-hash format', async () => {
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: VALID_LICENSE_KEY, install_id_hash: 'short' });
    assert.equal(res.status, 400);
    assert.equal(res.body.message, 'invalid_install_id_hash');
  });

  test('rejects unknown license key (no row in license_keys)', async () => {
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: VALID_LICENSE_KEY, install_id_hash: installIdHashFor('whatever-id-here-1234567890123456') });
    assert.equal(res.status, 403);
    assert.equal(res.body.error, 'invalid_license');
  });

  test('rejects inactive license', async () => {
    const seed = seedActiveLicense({ status: 'revoked' });
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash });
    assert.equal(res.status, 403);
    assert.equal(res.body.error, 'license_inactive');
  });

  test('rejects expired license', async () => {
    const seed = seedActiveLicense({ expiresAt: new Date(Date.now() - 60_000).toISOString() });
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash });
    assert.equal(res.status, 403);
    assert.equal(res.body.error, 'license_expired');
  });

  test('rejects when no device_binding exists for that key', async () => {
    const keyId = licenseKeyIdFor(VALID_LICENSE_KEY);
    mem.license_keys.push({
      id: keyId, prefix: 'DENG-1A2B', suffix: '7890',
      owner_discord_id: 'disc-owner', status: 'active',
    });
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: VALID_LICENSE_KEY, install_id_hash: 'b'.repeat(64) });
    assert.equal(res.status, 403);
    assert.equal(res.body.error, 'device_not_bound');
  });

  test('rejects when install_id_hash does not match the binding', async () => {
    const seed = seedActiveLicense();
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: installIdHashFor('different-install-id-1234567890ab') });
    assert.equal(res.status, 403);
    assert.equal(res.body.error, 'install_id_mismatch');
  });

  test('rejects when device_binding is inactive', async () => {
    const seed = seedActiveLicense({ bindingActive: false });
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash });
    assert.equal(res.status, 403);
    assert.equal(res.body.error, 'device_binding_inactive');
  });

  test('valid license + matching install_id_hash creates device and issues token', async () => {
    const seed = seedActiveLicense({ owner: 'disc-success-1' });
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({
        license_key: seed.key,
        install_id_hash: seed.installIdHash,
        device_label: 'My Cloud Phone',
        tool_version: '1.0.0',
        channel: 'stable',
      });
    assert.equal(res.status, 200);
    assert.ok(res.body.bridge_token, 'bridge_token returned');
    assert.ok(res.body.device_id, 'device_id returned');
    assert.ok(res.body.expires_at, 'expires_at returned');

    // The plaintext token must NEVER be stored — only its sha256 hash.
    assert.equal(mem.monitor_bridge_tokens.length, 1);
    const stored = mem.monitor_bridge_tokens[0];
    assert.equal(stored.token_hash, sha256(res.body.bridge_token));
    assert.notEqual(stored.token_hash, res.body.bridge_token);

    // The device row must be owned by the license owner (NOT some other
    // user) and must use the install_id_hash as the fingerprint hash.
    assert.equal(mem.monitor_devices.length, 1);
    const dev = mem.monitor_devices[0];
    assert.equal(dev.owner_discord_user_id, 'disc-success-1');
    assert.equal(dev.device_fingerprint_hash, seed.installIdHash);
    assert.equal(dev.device_label, 'My Cloud Phone');
    assert.equal(dev.tool_version, '1.0.0');
    assert.equal(dev.channel, 'stable');

    // A default settings row is created so the APK does not 500 on
    // first read.
    assert.equal(mem.monitor_settings.length, 1);
  });

  test('repeated calls reuse the same device row instead of duplicating', async () => {
    const seed = seedActiveLicense({ owner: 'disc-idempotent' });
    await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash, device_label: 'A' });
    await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash, device_label: 'B' });
    assert.equal(mem.monitor_devices.length, 1, 'same device row reused across runs');
    assert.equal(mem.monitor_devices[0].device_label, 'B', 'label refreshed on second call');
    assert.equal(mem.monitor_bridge_tokens.length, 2, 'new bridge token issued each call');
  });

  test('different owner cannot mint a token for the same device (cross-user safety)', async () => {
    // Owner-A is the real owner of the device.
    const seedA = seedActiveLicense({ owner: 'disc-owner-A' });
    // Owner-B owns a different license entirely, bound to a different install.
    const seedB = seedActiveLicense({
      owner: 'disc-owner-B',
      key: 'DENG-AAAA-BBBB-CCCC-DDDD',
      installId: 'b'.repeat(32),
    });

    // Owner-B tries to claim Owner-A's install_id_hash by passing it as their own.
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seedB.key, install_id_hash: seedA.installIdHash });
    assert.equal(res.status, 403);
    assert.equal(res.body.error, 'install_id_mismatch');

    // No device row for owner-B was created.
    const ownersB = mem.monitor_devices.filter((d) => d.owner_discord_user_id === 'disc-owner-B');
    assert.equal(ownersB.length, 0);
  });

  test('the issued bridge_token is accepted by /api/monitor/bridge/push end-to-end', async () => {
    const seed = seedActiveLicense({ owner: 'disc-e2e' });
    const issue = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash });
    assert.equal(issue.status, 200);
    const token = issue.body.bridge_token;

    // Empty packages push must still succeed and flip status_connected=true,
    // so the APK shows the device even before Start.
    const push = await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({ schema: 1, tool_version: '1.0.0', channel: 'stable', packages: [] });
    assert.equal(push.status, 200);

    const dev = mem.monitor_devices.find((d) => d.owner_discord_user_id === 'disc-e2e');
    assert.ok(dev, 'device exists');
    assert.equal(dev.status_connected, true, 'device flipped to connected after first push');
  });

  test('an APK app-session for the same owner can now see the device', async () => {
    const seed = seedActiveLicense({ owner: 'disc-pair-flow' });
    await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash });

    const appToken = seedAppSession('disc-pair-flow');
    const res = await request(app)
      .get('/api/monitor/devices')
      .set('Authorization', `Bearer ${appToken}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.devices.length, 1, 'APK sees the device registered by Termux');
  });

  test('payload never accepts banned fields back', async () => {
    const seed = seedActiveLicense({ owner: 'disc-scrub' });
    const res = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({
        license_key: seed.key,
        install_id_hash: seed.installIdHash,
        // attempt to smuggle a malicious owner_discord_user_id:
        owner_discord_user_id: 'disc-attacker',
        // attempt to override the channel with a bogus value:
        channel: 'pwn',
      });
    assert.equal(res.status, 200);
    const dev = mem.monitor_devices[0];
    // Owner must come from license_keys, never from the request body.
    assert.equal(dev.owner_discord_user_id, 'disc-scrub');
    // Unknown channel falls back to "stable".
    assert.equal(dev.channel, 'stable');
  });
});


// ── v1.0.4 — connection TTL, bridge_status, new state vocabulary ─────────────

describe('v1.0.9 connection_state TTL (interval-scaled)', () => {
  const { computeConnectionState, connectionThresholds, connectionTtlSeconds, DEVICE_CONNECTION_TTL_SECONDS } =
    require('../src/monitorRoutes.js').__test__;

  test('fresh last_seen_at → Connected', () => {
    const r = computeConnectionState(new Date().toISOString(), 5);
    assert.equal(r.connected, true);
    assert.equal(r.connection_state, 'Connected');
    assert.ok(typeof r.seconds_since_last_seen === 'number');
    assert.ok(r.seconds_since_last_seen >= 0 && r.seconds_since_last_seen < 5);
  });

  test('30s interval: still Connected before stale threshold', () => {
    const seen = new Date(Date.now() - 45 * 1000).toISOString();
    const r = computeConnectionState(seen, 30);
    assert.equal(r.connected, true);
    assert.equal(r.connection_state, 'Connected');
    assert.equal(connectionTtlSeconds(30), 90);
    assert.deepEqual(connectionThresholds(30), {
      stale_after_seconds: 60,
      disconnected_after_seconds: 90,
    });
  });

  test('30s interval: Stale after two missed pushes but not Disconnected', () => {
    const seen = new Date(Date.now() - 75 * 1000).toISOString();
    const r = computeConnectionState(seen, 30);
    assert.equal(r.connected, true);
    assert.equal(r.connection_state, 'Stale');
    assert.equal(r.stale_after_seconds, 60);
    assert.equal(r.disconnected_after_seconds, 90);
  });

  test('30s interval: Disconnected after TTL (90s)', () => {
    const stale = new Date(Date.now() - 95 * 1000).toISOString();
    const r = computeConnectionState(stale, 30);
    assert.equal(r.connected, false);
    assert.equal(r.connection_state, 'Disconnected');
    assert.ok(r.seconds_since_last_seen > connectionTtlSeconds(30));
  });

  test('null last_seen_at → Disconnected', () => {
    const r = computeConnectionState(null, 30);
    assert.equal(r.connected, false);
    assert.equal(r.connection_state, 'Disconnected');
    assert.equal(r.seconds_since_last_seen, null);
  });

  test('legacy DEVICE_CONNECTION_TTL_SECONDS constant kept for compat', () => {
    assert.equal(DEVICE_CONNECTION_TTL_SECONDS, 30);
  });
});

describe('v1.0.4 summarizePackages — new state vocabulary', () => {
  const { summarizePackages } = require('../src/monitorRoutes.js').__test__;

  test('counts Launching, Joining, and No Heartbeat separately', () => {
    const rows = [
      { state: 'Online', ram_mb: 100 },
      { state: 'Launching', ram_mb: 50 },
      { state: 'Joining', ram_mb: 60 },
      { state: 'No Heartbeat', ram_mb: 70 },
      { state: 'Dead', ram_mb: 0 },
      { state: 'Relaunching', ram_mb: 0 }, // legacy → counted in both launching and relaunching
    ];
    const s = summarizePackages(rows);
    assert.equal(s.total, 6);
    assert.equal(s.online, 1);
    assert.equal(s.dead, 1);
    assert.equal(s.launching, 2, 'Launching + legacy Relaunching both count');
    assert.equal(s.joining, 1);
    assert.equal(s.no_heartbeat, 1);
    assert.equal(s.relaunching, 1, 'legacy counter preserved for old APKs');
  });

  test('In-Lobby (if anyone sends it) lands in "other", never in dead/launching/joining', () => {
    const s = summarizePackages([{ state: 'In-Lobby', ram_mb: 0 }]);
    assert.equal(s.other, 1);
    assert.equal(s.dead, 0);
    assert.equal(s.launching, 0);
    assert.equal(s.joining, 0);
  });
});

describe('v1.0.4 /status endpoint exposes connection_state and last_bridge_status', () => {
  const { connectionTtlSeconds } = require('../src/monitorRoutes.js').__test__;
  beforeEach(() => { mem = makeMemoryDb(); });

  test('/status returns Disconnected after TTL even though status_connected was sticky-true', async () => {

    const seed = seedActiveLicense({ owner: 'disc-ttl' });
    const issue = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash });
    const token = issue.body.bridge_token;

    // First push flips status_connected to true.
    await request(app).post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({ schema: 1, tool_version: '1.0.0', channel: 'stable', packages: [] });

    const dev = mem.monitor_devices[0];
    assert.equal(dev.status_connected, true, 'sticky boolean was set');
    const intervalSec = 5;
    const ttl = connectionTtlSeconds(intervalSec);
    // Backdate well past the interval-scaled TTL (default refresh interval is 5s).
    dev.last_seen_at = new Date(Date.now() - (ttl + 10) * 1000).toISOString();

    const appToken = seedAppSession('disc-ttl');
    const status = await request(app)
      .get(`/api/monitor/devices/${dev.id}/status`)
      .set('Authorization', `Bearer ${appToken}`);
    assert.equal(status.status, 200);
    assert.equal(
      status.body.device.connected, false,
      'computed connected boolean overrides sticky status_connected',
    );
    assert.equal(status.body.device.connection_state, 'Disconnected');
    assert.ok(status.body.device.seconds_since_last_seen > ttl);
    assert.equal(status.body.device.stale_after_seconds, 60);
    assert.equal(status.body.device.disconnected_after_seconds, 90);
    // Legacy sticky field is still surfaced unmodified for back-compat.
    assert.equal(status.body.device.status_connected, true);
  });

  test('/push accepts bridge_status and /status echoes it back', async () => {
    const seed = seedActiveLicense({ owner: 'disc-bs' });
    const issue = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash });
    const token = issue.body.bridge_token;

    const push = await request(app).post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({
        schema: 1, tool_version: '1.0.0', channel: 'stable',
        packages: [],
        bridge_status: {
          snapshot_last_result: 'capture_failed',
          snapshot_last_bytes: 0,
          snapshot_last_error: 'screencap_unavailable',
          snapshot_provider_called_count: 7,
          snapshot_last_upload_status: null,
          screencap_available: false,
          last_push_result: 'success',
          last_push_error: 'http_429_retry_after_60',
          next_retry_at: '2026-05-30T03:00:00Z',
          // Banned field — must be stripped server-side.
          bridge_token: 'leaking-token',
          license_key: 'DENG-XXXX',
        },
      });
    assert.equal(push.status, 200);

    const dev = mem.monitor_devices[0];
    const bs = dev.last_bridge_status;
    assert.ok(bs, 'bridge_status persisted to device row');
    assert.equal(bs.snapshot_last_result, 'capture_failed');
    assert.equal(bs.snapshot_last_error, 'screencap_unavailable');
    assert.equal(bs.snapshot_provider_called_count, 7);
    assert.equal(bs.screencap_available, false);
    assert.equal(bs.last_push_result, 'success');
    assert.equal(bs.last_push_error, 'http_429_retry_after_60');
    assert.equal(bs.next_retry_at, '2026-05-30T03:00:00Z');
    // Allow-list is enforced — secrets dropped.
    assert.equal(bs.bridge_token, undefined);
    assert.equal(bs.license_key, undefined);
    assert.equal(bs.schema, 1, 'schema marker present');

    const appToken = seedAppSession('disc-bs');
    const status = await request(app)
      .get(`/api/monitor/devices/${dev.id}/status`)
      .set('Authorization', `Bearer ${appToken}`);
    assert.equal(status.status, 200);
    assert.equal(status.body.device.last_bridge_status.snapshot_last_result, 'capture_failed');
    assert.equal(status.body.device.last_push_status, 'success');
    assert.equal(status.body.device.last_push_error, 'http_429_retry_after_60');
    assert.equal(status.body.device.next_retry_at, '2026-05-30T03:00:00Z');
  });
});

// ── v1.0.6 — snapshot robustness + device-centric dashboard fields ───────────

describe('v1.0.6 snapshot upload + heartbeat independence', () => {
  beforeEach(() => { mem = makeMemoryDb(); });

  const PNG = Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    Buffer.alloc(12_000, 0x42),
  ]);

  test('valid PNG snapshot upload is stored and returns success JSON', async () => {
    const deviceId = seedDevice('disc-snap');
    const token = seedBridgeToken(deviceId);
    const res = await request(app)
      .post('/api/monitor/bridge/snapshot')
      .set('Authorization', `Bearer ${token}`)
      .set('Content-Type', 'image/png')
      .send(PNG);
    assert.equal(res.status, 200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.size, PNG.length);
    assert.equal(mem.monitor_snapshots.length, 1);
    assert.equal(mem.monitor_snapshots[0].mime_type, 'image/png');
  });

  test('heartbeat succeeds when bridge_status (snapshot fields) is missing', async () => {
    const deviceId = seedDevice('disc-noss');
    const token = seedBridgeToken(deviceId);
    const res = await request(app)
      .post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({ schema: 1, tool_version: '1.0.6', channel: 'stable', packages: [] });
    assert.equal(res.status, 200);
    assert.equal(mem.monitor_devices[0].status_connected, true);
  });

  test('heartbeat persists v1.0.6 capture-provider diagnostics + device_ram', async () => {
    const seed = seedActiveLicense({ owner: 'disc-ram' });
    const issue = await request(app)
      .post('/api/monitor/bridge/issue-from-license')
      .send({ license_key: seed.key, install_id_hash: seed.installIdHash });
    const token = issue.body.bridge_token;

    const push = await request(app).post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({
        schema: 1, tool_version: '1.0.6', channel: 'stable', packages: [],
        bridge_status: {
          snapshot_last_result: 'success',
          snapshot_provider: 'root_screencap_file',
          snapshot_png_valid: true,
          snapshot_root_granted: true,
          snapshot_su_available: true,
          device_ram: { available_mb: 2048, total_mb: 4096, percent: 50 },
          last_push_result: 'success',
        },
      });
    assert.equal(push.status, 200);
    const bs = mem.monitor_devices[0].last_bridge_status;
    assert.equal(bs.snapshot_provider, 'root_screencap_file');
    assert.equal(bs.snapshot_png_valid, true);
    assert.equal(bs.snapshot_root_granted, true);
    assert.deepEqual(bs.device_ram, { available_mb: 2048, total_mb: 4096, percent: 50 });
  });

  test('snapshot upload failure (oversized) does not break the heartbeat', async () => {
    const deviceId = seedDevice('disc-indep');
    const token = seedBridgeToken(deviceId);
    // Heartbeat 1 — connected.
    await request(app).post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({ schema: 1, tool_version: '1.0.6', channel: 'stable', packages: [] });
    // Oversized snapshot — rejected.
    const big = Buffer.alloc(6 * 1024 * 1024, 0xff);
    const snap = await request(app).post('/api/monitor/bridge/snapshot')
      .set('Authorization', `Bearer ${token}`)
      .set('Content-Type', 'image/png')
      .send(big);
    assert.ok(snap.status === 413 || snap.status === 400);
    // Heartbeat 2 — still works, device still connected.
    const push2 = await request(app).post('/api/monitor/bridge/push')
      .set('Authorization', `Bearer ${token}`)
      .send({ schema: 1, tool_version: '1.0.6', channel: 'stable', packages: [] });
    assert.equal(push2.status, 200);
    assert.equal(mem.monitor_devices[0].status_connected, true);
  });

  test('snapshot latest returns 204 when no snapshot exists', async () => {
    const owner = 'disc-empty-snap';
    const deviceId = seedDevice(owner);
    const appToken = seedAppSession(owner);
    const res = await request(app)
      .get(`/api/monitor/devices/${deviceId}/snapshot/latest`)
      .set('Authorization', `Bearer ${appToken}`);
    assert.equal(res.status, 204);
  });

  test('bridge latest snapshot returns uploaded bytes for the same device', async () => {
    const deviceId = seedDevice('disc-bridge-snap');
    const token = seedBridgeToken(deviceId);
    const upload = await request(app)
      .post('/api/monitor/bridge/snapshot')
      .set('Authorization', `Bearer ${token}`)
      .set('Content-Type', 'image/png')
      .send(PNG);
    assert.equal(upload.status, 200);

    const latest = await request(app)
      .get('/api/monitor/bridge/snapshot/latest')
      .set('Authorization', `Bearer ${token}`);
    assert.equal(latest.status, 200);
    assert.equal(latest.headers['content-type'], 'image/png');
    assert.equal(Buffer.compare(Buffer.from(latest.body), PNG), 0);
  });
});

describe('v1.0.6 device-centric dashboard list fields', () => {
  beforeEach(() => { mem = makeMemoryDb(); });

  test('list returns connection state + device_ram + snapshot result per device', async () => {
    const owner = 'disc-dash';
    const deviceId = seedDevice(owner);
    mem.monitor_devices[0].last_bridge_status = {
      device_ram: { available_mb: 1500, total_mb: 3000, percent: 50 },
      snapshot_last_result: 'success',
    };
    const appToken = seedAppSession(owner);
    const res = await request(app)
      .get('/api/monitor/devices')
      .set('Authorization', `Bearer ${appToken}`);
    assert.equal(res.status, 200);
    const d = res.body.devices[0];
    // Connection fields drive TOTAL/ONLINE/DEAD on the client.
    assert.ok('connected' in d);
    assert.ok('connection_state' in d);
    assert.equal(d.device_ram.total_mb, 3000);
    assert.equal(d.device_ram.available_mb, 1500);
    assert.equal(d.device_ram.percent, 50);
    assert.equal(d.snapshot_last_result, 'success');
    // last_bridge_status (the heavy blob) is not leaked into the list row.
    assert.equal(d.last_bridge_status, undefined);
  });

  test('list tolerates devices that never reported RAM (no invented numbers)', async () => {
    const owner = 'disc-noram';
    seedDevice(owner);
    const appToken = seedAppSession(owner);
    const res = await request(app)
      .get('/api/monitor/devices')
      .set('Authorization', `Bearer ${appToken}`);
    assert.equal(res.status, 200);
    assert.equal(res.body.devices[0].device_ram, null);
  });
});
