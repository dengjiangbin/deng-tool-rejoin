'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const crypto = require('crypto');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-public-stats';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';

const memoryDb = {
  site_users: [],
  license_users: [],
  license_keys: [],
  device_bindings: [],
};

class MemoryQuery {
  constructor(table) {
    this.table = table;
    this.filters = [];
    this.action = 'select';
    this.payload = null;
  }

  select() { return this; }
  eq(field, value) { this.filters.push({ field, value }); return this; }
  maybeSingle() { return this._run().then(({ data }) => ({ data: data[0] || null, error: null })); }
  then(resolve, reject) { return this._run().then(resolve, reject); }

  _matches(row) {
    return this.filters.every((f) => row[f.field] === f.value);
  }

  async _run() {
    const rows = memoryDb[this.table] || [];
    return { data: rows.filter((row) => this._matches(row)), error: null };
  }
}

require.cache[path.join(__dirname, '..', 'src', 'db.js')] = {
  id: path.join(__dirname, '..', 'src', 'db.js'),
  filename: path.join(__dirname, '..', 'src', 'db.js'),
  loaded: true,
  exports: { from(table) { return new MemoryQuery(table); } },
};

const licenseService = require('../src/licenseService');
const buildPublicStatsPayload = licenseService._buildPublicStatsPayload;

describe('public stats unique users regression', () => {
  beforeEach(() => {
    memoryDb.site_users = [];
    memoryDb.license_users = [];
    memoryDb.license_keys = [];
    memoryDb.device_bindings = [];
    licenseService._clearPublicStatsCache?.();
  });

  test('counts 250 registered portal users without hardcoding', () => {
    for (let i = 0; i < 250; i += 1) {
      memoryDb.site_users.push({
        id: crypto.randomUUID(),
        discord_user_id: `discord-user-${i}`,
        is_active: true,
      });
    }
    const payload = buildPublicStatsPayload({
      keys: [],
      bindings: [],
      licenseUsers: [],
      siteUsers: memoryDb.site_users,
    });
    assert.equal(payload.uniqueUsers, 250);
    assert.ok(payload.uniqueUsers > 200);
    assert.equal(payload._internalSources.uniqueUsers.siteUsersCounted, 250);
  });

  test('does not use tracker online count semantics', () => {
    const payload = buildPublicStatsPayload({
      keys: [],
      bindings: [],
      licenseUsers: [],
      siteUsers: [{ id: 's1', discord_user_id: 'd1', is_active: true }],
    });
    assert.match(payload._internalSources.uniqueUsers.method, /site_users/i);
    assert.match(payload._internalSources.uniqueUsers.excludes.join(' '), /tracker/i);
  });
});
