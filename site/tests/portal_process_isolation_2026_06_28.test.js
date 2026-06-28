'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

process.env.TOOL_SITE_COOKIE_SECRET = 'test-cookie-secret-that-is-long-enough-for-portal-suite';
process.env.TOOL_SITE_STATE_SECRET = 'test-state-secret-that-is-long-enough-for-portal-suite';
process.env.SUPABASE_URL = 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = 'test-service-role-key';
process.env.NODE_ENV = 'test';

test('portal source files do not require fishitTrackerRoutes', () => {
  const portalAppSrc = fs.readFileSync(path.join(__dirname, '..', 'src', 'portalApp.js'), 'utf8');
  const portalServerSrc = fs.readFileSync(path.join(__dirname, '..', 'portal-server.js'), 'utf8');
  assert.doesNotMatch(portalAppSrc, /require\('\.\/fishitTrackerRoutes'\)/);
  assert.doesNotMatch(portalServerSrc, /trackerReadProxy/);
  assert.doesNotMatch(portalServerSrc, /shouldProxyTrackerRead/);
});

test('portal /healthz does not invoke tracker inventory session repair', async () => {
  process.env.SITE_APP_MODE = 'portal';
  process.env.PORTAL_MODE = '1';
  const inventorySession = require('../src/inventorySession');
  const orig = inventorySession.repairInventorySession;
  let repairCalls = 0;
  inventorySession.repairInventorySession = async () => { repairCalls += 1; };
  try {
    delete require.cache[require.resolve('../src/portalApp')];
    const portalApp = require('../src/portalApp');
    const http = require('http');
    const server = http.createServer(portalApp);
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const addr = server.address();
    const res = await fetch(`http://127.0.0.1:${addr.port}/healthz`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.service, 'deng-portal-license');
    server.close();
    assert.equal(repairCalls, 0);
  } finally {
    inventorySession.repairInventorySession = orig;
  }
});

test('tracker site source still mounts fishitTrackerRoutes', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'trackerSiteApp.js'), 'utf8');
  assert.match(src, /fishitTrackerRoutes/);
  assert.doesNotMatch(src, /require\('\.\/routes'\)/);
});
