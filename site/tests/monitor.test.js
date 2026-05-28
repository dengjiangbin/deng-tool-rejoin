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

// ── Tiny Supabase mock with monitor-table support ───────────────────────────
function makeMemoryDb() {
  return {
    monitor_devices: [],
    monitor_package_states: [],
    monitor_snapshots: [],
    monitor_settings: [],
    monitor_bridge_tokens: [],
    monitor_pairing_codes: [],
    monitor_app_sessions: [],
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
  _matches(row) { return this.filters.every((f) => row[f.field] === f.value); }

  select(cols) { if (cols) this.selectColumns = cols; return this; }
  insert(payload) { this.action = 'insert'; this.payload = Array.isArray(payload) ? payload : [payload]; return this; }
  update(payload) { this.action = 'update'; this.payload = payload; return this; }
  upsert(payload, opts = {}) { this.action = 'upsert'; this.payload = Array.isArray(payload) ? payload : [payload]; this.upsertOnConflict = opts.onConflict || null; return this; }
  delete() { this.action = 'delete'; return this; }
  eq(field, value) { this.filters.push({ field, value }); return this; }
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

  test('rejects oversized snapshot', async () => {
    const deviceId = seedDevice();
    const token = seedBridgeToken(deviceId);
    const big = Buffer.alloc(2_000_000, 0xff); // 2MB > 1.5MB limit
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

  test('cannot read another user device status', async () => {
    const myToken = seedAppSession('disc-me');
    const othersDevice = seedDevice('disc-other');
    const res = await request(app)
      .get(`/api/monitor/devices/${othersDevice}/status`)
      .set('Authorization', `Bearer ${myToken}`);
    assert.equal(res.status, 404);
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
  test('renders /download with new product name and primary CTA', async () => {
    const res = await request(app).get('/download');
    assert.equal(res.status, 200);
    assert.match(res.text, /DENG Tool: Rejoin APK/);
    assert.match(res.text, />Download APK</);
    assert.match(res.text, /Install Instructions/);
  });

  test('/download does NOT show legacy "DENG Monitor" naming', async () => {
    const res = await request(app).get('/download');
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

  test('canonical latest alias returns friendly 404 when no APK published', async () => {
    const res = await request(app).get('/downloads/deng-tool-rejoin-apk-latest.apk');
    assert.equal(res.status, 404);
    assert.match(res.text, /APK not available yet/);
  });

  test('legacy latest alias permanently redirects to new latest alias', async () => {
    const res = await request(app)
      .get('/downloads/deng-monitor-latest.apk')
      .redirects(0);
    assert.equal(res.status, 301);
    assert.equal(res.headers.location, '/downloads/deng-tool-rejoin-apk-latest.apk');
  });

  test('legacy versioned filename redirects to new pattern when missing on disk', async () => {
    const res = await request(app)
      .get('/downloads/deng-monitor-v1.0.0.apk')
      .redirects(0);
    assert.equal(res.status, 301);
    assert.equal(res.headers.location, '/downloads/deng-tool-rejoin-apk-v1.0.0.apk');
  });

  test('new versioned filename returns 404 (file not on disk in tests)', async () => {
    const res = await request(app).get('/downloads/deng-tool-rejoin-apk-v1.0.0.apk');
    assert.equal(res.status, 404);
  });

  test('traversal attempt via legacy filename pattern is rejected', async () => {
    // Even if the legacy regex matches, the resolved-path defense must block.
    const res = await request(app).get('/downloads/deng-monitor-..%2F..%2Fpackage.json');
    assert.equal(res.status, 404);
  });
});
