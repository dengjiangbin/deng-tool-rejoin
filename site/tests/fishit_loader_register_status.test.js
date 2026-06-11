'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-the-site-suite';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID || 'test-discord-client-id';
process.env.DISCORD_CLIENT_SECRET = process.env.DISCORD_CLIENT_SECRET || 'test-discord-client-secret';
process.env.DISCORD_REDIRECT_URI = process.env.DISCORD_REDIRECT_URI || 'http://localhost:8791/auth/discord/callback';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const trackerRoutes = require('../src/fishitTrackerRoutes');
const {
  LOADER_REGISTER_LIMIT_FIX_BUILD,
  MINIMUM_TRACKER_BUILD,
} = require('../src/fishitTrackerBuild');
const { CLEAN_TRACKER_LOADSTRING } = require('../src/fishitTrackerLoadstring');

const {
  deriveConnectionStatus,
  applyUploadSyncSuccess,
  applyUploadSyncFailure,
  UPLOAD_INTERVAL_SECONDS,
  UPLOAD_GRACE_SECONDS,
} = trackerRoutes;

describe('loader register fix + upload-sync status contract', () => {
  test('minimum tracker build is LOADER_REGISTER_LIMIT_FIX marker', () => {
    assert.equal(MINIMUM_TRACKER_BUILD, 'LOADER_REGISTER_LIMIT_FIX_2026_06_11');
    assert.equal(LOADER_REGISTER_LIMIT_FIX_BUILD, MINIMUM_TRACKER_BUILD);
  });

  test('public loader copy is clean fish-it URL without cache-bust query', () => {
    assert.equal(
      CLEAN_TRACKER_LOADSTRING,
      'loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua"))()',
    );
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /\?v=/);
  });

  test('green only within lastSuccessfulUploadAt + interval + grace', () => {
    const now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const fresh = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastSuccessfulUploadAt: iso(now - 5000),
      intervalSeconds: UPLOAD_INTERVAL_SECONDS,
      graceSeconds: UPLOAD_GRACE_SECONDS,
    };
    const live = deriveConnectionStatus(fresh);
    assert.equal(live.currentStatus, 'green');
    assert.equal(live.connectionStatus, 'live');
    assert.equal(trackerRoutes.isSessionLive(fresh), true);

    const missed = deriveConnectionStatus({
      ...fresh,
      lastSuccessfulUploadAt: iso(now - (UPLOAD_INTERVAL_SECONDS + UPLOAD_GRACE_SECONDS + 5) * 1000),
    });
    assert.equal(missed.currentStatus, 'red');
    assert.equal(missed.connectionStatusReason, 'upload_interval_missed');
    assert.ok(missed.redDurationSeconds >= 0);
  });

  test('heartbeat alone never turns green', () => {
    const now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const hbOnly = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastHeartbeatAt: iso(now - 1000),
      lastSeenAt: iso(now - 1000),
    };
    const st = deriveConnectionStatus(hbOnly);
    assert.equal(st.currentStatus, 'red');
    assert.notEqual(st.connectionStatus, 'live');
    assert.equal(trackerRoutes.isSessionLive(hbOnly), false);
  });

  test('outdated loader build stays red even with recent upload timestamp', () => {
    const now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const old = {
      trackerBuild: 'LOADER_FIX_REGISTER_LIMIT_2026_06_11',
      lastSuccessfulUploadAt: iso(now - 1000),
    };
    const st = deriveConnectionStatus(old);
    assert.equal(st.currentStatus, 'red');
    assert.equal(st.connectionStatusReason, 'outdated_loader');
  });

  test('success clears redSince; failure sets redSince; recovery goes green', () => {
    const now = new Date().toISOString();
    let session = applyUploadSyncFailure({}, now, 'interval_upload_failed');
    assert.equal(session.currentStatus, 'red');
    assert.ok(session.redSince);

    session = applyUploadSyncSuccess(session, now, { payloadHash: 'abc123' });
    assert.equal(session.currentStatus, 'green');
    assert.equal(session.redSince, null);
    assert.ok(session.lastSuccessfulUploadAt);
  });

  test('register-limit loader error surfaces red status', () => {
    const now = Date.now();
    const st = deriveConnectionStatus({
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastLoaderErrorAt: new Date(now - 1000).toISOString(),
      lastLoaderErrorMessage: 'Out of local registers when trying to allocate main: exceeded limit 200',
      redSince: new Date(now - 1000).toISOString(),
    });
    assert.equal(st.currentStatus, 'red');
    assert.equal(st.connectionStatusReason, 'loader_register_limit');
  });
});
