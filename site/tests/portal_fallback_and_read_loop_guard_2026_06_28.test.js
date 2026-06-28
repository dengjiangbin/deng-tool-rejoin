'use strict';

const test = require('node:test');
const assert = require('node:assert');

const { shouldProxyToPortal } = require('../src/portalFallbackProxy');
const { shouldProxyTrackerRead } = require('../src/trackerReadProxy');
const { preBindReclaimSingleOwner } = require('../src/reclaimPort');

test('portal fallback proxy routes portal-owned paths but never tracker paths', () => {
  const portalPaths = [
    '/license', '/dashboard', '/download', '/downloads/x.apk', '/stats', '/app',
    '/api/license/download', '/api/key/start',
    // Homepage Platform Stats lives on the portal (8790); the 8791 catch-all
    // must forward it or the homepage Platform Stats render empty.
    '/api/public-stats', '/api/public-stats?x=1', '/api/stats/public',
  ];
  for (const url of portalPaths) {
    assert.strictEqual(shouldProxyToPortal({ url }), true, `expected portal proxy for ${url}`);
  }
  const trackerPaths = [
    '/tracker', '/', '/api/tracker/latest/foo', '/api/fishit-tracker/get-backpack/foo',
    '/css/app.css',
    // The homepage Live Network feed is served by 8791 itself — must NOT proxy.
    '/api/public/tracker-stats',
  ];
  for (const url of trackerPaths) {
    assert.strictEqual(shouldProxyToPortal({ url }), false, `expected NO portal proxy for ${url}`);
  }
});

test('read proxy loop guard: a fallback-tagged request is never bounced back to 8793', () => {
  const base = { method: 'GET', url: '/api/tracker/latest/foo', headers: {} };
  assert.strictEqual(shouldProxyTrackerRead(base), true, 'normal read should proxy to 8793');
  const looped = { method: 'GET', url: '/api/tracker/latest/foo', headers: { 'x-deng-read-fallback': '1' } };
  assert.strictEqual(shouldProxyTrackerRead(looped), false, 'fallback-tagged read must NOT re-proxy (loop breaker)');
});

test('pre-bind single-owner reclaim kills a non-self node listener and skips self/non-node', () => {
  const killed = [];
  const result = preBindReclaimSingleOwner(8793, '[test]', {
    _platform: 'win32',
    _selfPid: 100,
    _findListenerPids: () => [100, 200, 300],
    _describeProcess: (pid) => (pid === 300 ? { pid, name: 'chrome.exe' } : { pid, name: 'node.exe' }),
    _killPid: (pid) => { killed.push(pid); return true; },
  });
  assert.strictEqual(result, 1, 'should kill exactly one holder (200)');
  assert.deepStrictEqual(killed, [200], 'self (100) skipped, node orphan (200) killed, non-node (300) spared');
});
