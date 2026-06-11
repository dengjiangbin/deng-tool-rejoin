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
  LOADER_FIX_REGISTER_LIMIT_BUILD,
  MINIMUM_TRACKER_BUILD,
} = require('../src/fishitTrackerBuild');
const { CLEAN_TRACKER_LOADSTRING } = require('../src/fishitTrackerLoadstring');

describe('loader register fix + strict connection status', () => {
  test('minimum tracker build is register-limit fix marker', () => {
    assert.equal(MINIMUM_TRACKER_BUILD, 'LOADER_FIX_REGISTER_LIMIT_2026_06_11');
    assert.equal(LOADER_FIX_REGISTER_LIMIT_BUILD, MINIMUM_TRACKER_BUILD);
  });

  test('public loader copy uses cache-busted fish-it URL', () => {
    assert.match(CLEAN_TRACKER_LOADSTRING, /tracker\.lua\?v=LOADER_FIX_REGISTER_LIMIT_2026_06_11/);
  });

  test('green status requires fresh stats upload not heartbeat alone', () => {
    const now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const staleHeartbeat = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastHeartbeatAt: iso(now - 5000),
      lastSeenAt: iso(now - 5000),
      lastInventoryAt: iso(now - 120000),
      playerStatsUpdatedAt: iso(now - 120000),
    };
    const st = trackerRoutes.deriveConnectionStatus(staleHeartbeat);
    assert.equal(st.connectionStatus, 'stale');
    assert.equal(st.connectionStatusColor, 'yellow');
    assert.equal(st.connectionStatusMessage, 'Heartbeat only, stats stale');
    assert.equal(trackerRoutes.isSessionLive(staleHeartbeat), false);

    const freshStats = {
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastHeartbeatAt: iso(now - 5000),
      lastStatsUploadAt: iso(now - 8000),
      playerStatsUpdatedAt: iso(now - 8000),
      lastSnapshotUploadAt: iso(now - 8000),
      lastInventoryAt: iso(now - 8000),
    };
    const live = trackerRoutes.deriveConnectionStatus(freshStats);
    assert.equal(live.connectionStatus, 'live');
    assert.equal(live.connectionStatusColor, 'green');
    assert.match(live.connectionStatusMessage, /Fresh stats updated/);
    assert.equal(trackerRoutes.isSessionLive(freshStats), true);
  });

  test('outdated loader build is error not green', () => {
    const now = Date.now();
    const iso = (ms) => new Date(ms).toISOString();
    const old = {
      trackerBuild: 'NEW_FISH_IT_ONLY_2026_06_11',
      lastHeartbeatAt: iso(now - 1000),
      lastStatsUploadAt: iso(now - 1000),
    };
    const st = trackerRoutes.deriveConnectionStatus(old);
    assert.equal(st.connectionStatus, 'error');
    assert.equal(st.connectionStatusReason, 'outdated_loader');
    assert.equal(st.connectionStatusMessage, 'Outdated cached loader running');
  });

  test('register-limit loader error surfaces explicit message', () => {
    const now = Date.now();
    const st = trackerRoutes.deriveConnectionStatus({
      trackerBuild: MINIMUM_TRACKER_BUILD,
      lastLoaderErrorAt: new Date(now - 1000).toISOString(),
      lastLoaderErrorMessage: 'Out of local registers when trying to allocate main: exceeded limit 200',
    });
    assert.equal(st.connectionStatus, 'error');
    assert.equal(st.connectionStatusReason, 'loader_register_limit');
    assert.equal(st.connectionStatusMessage, 'Loader failed: register limit exceeded');
  });
});
