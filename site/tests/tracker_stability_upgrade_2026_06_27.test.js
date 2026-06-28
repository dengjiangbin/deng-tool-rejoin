'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  shouldProxyTrackerRead,
  isTrackerReadHealthPath,
  handleTrackerReadHealth,
  READ_PROXY_PREFIXES,
} = require('../src/trackerReadProxy');

function req(method, url) {
  return { method, url };
}

test('shouldProxyTrackerRead forwards tracker polls to read lane', () => {
  assert.ok(isTrackerReadHealthPath('/api/tracker/read-health'));
  assert.equal(shouldProxyTrackerRead(req('GET', '/api/tracker/read-health')), false);
  assert.ok(shouldProxyTrackerRead(req('GET', '/api/tracker/get-backpack/dengerous1820')));
  assert.ok(shouldProxyTrackerRead(req('GET', '/api/tracker/get-backpack/dengerous1820?lite=1')));
  assert.ok(shouldProxyTrackerRead(req('GET', '/api/tracker/account-status?owner=1')));
  assert.ok(shouldProxyTrackerRead(req('GET', '/api/fishit-tracker/get-backpack/foo')));
  assert.equal(shouldProxyTrackerRead(req('POST', '/api/tracker/get-backpack/x')), false);
  assert.equal(shouldProxyTrackerRead(req('GET', '/license')), false);
  assert.equal(shouldProxyTrackerRead(req('GET', '/api/tracker/dashboard')), false);
  assert.ok(READ_PROXY_PREFIXES.length >= 6);
});

test('handleTrackerReadHealth responds without proxying heavy readHealth body', () => {
  const http = require('http');
  const server = http.createServer((req, res) => {
    if (req.url === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'ok', service: 'deng-tracker-read', port: 8793 }));
      return;
    }
    res.writeHead(404);
    res.end();
  });
  return new Promise((resolve, reject) => {
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address();
      const fakeReq = { method: 'GET', url: '/api/tracker/read-health' };
      const fakeRes = {
        statusCode: 0,
        headers: {},
        writeHead(code, headers) {
          this.statusCode = code;
          this.headers = headers || {};
        },
        body: '',
        end(body) {
          this.body = body;
          server.close(() => {
            try {
              assert.equal(this.statusCode, 200);
              assert.equal(this.headers['X-DENG-Tracker-Read-Health'], 'web-light-probe');
              const parsed = JSON.parse(this.body);
              assert.equal(parsed.status, 'ok');
              assert.equal(parsed.probe, 'tracker-read-health');
              assert.equal(parsed.upstream.service, 'deng-tracker-read');
              resolve();
            } catch (err) {
              reject(err);
            }
          });
        },
      };
      handleTrackerReadHealth(fakeReq, fakeRes, {
        host: '127.0.0.1',
        port: addr.port,
        probeMs: 2000,
      });
    });
    server.on('error', reject);
  });
});

test('flushSessionImmediate full mode uses account shard not presence-only', () => {
  const sharded = require('../src/fishitSessionStoreSharded');
  const store = require('../src/fishitSessionStore');
  const origUse = sharded.useShardedStorage;
  const origFlushAccount = sharded.flushAccountSync;
  const origPresence = sharded.flushPresenceHeartbeatSync;
  let accountCalls = 0;
  let presenceCalls = 0;
  sharded.useShardedStorage = () => true;
  sharded.flushAccountSync = () => { accountCalls += 1; return { flushed: true }; };
  sharded.flushPresenceHeartbeatSync = () => { presenceCalls += 1; return { flushed: true }; };
  try {
    const row = {
      username: 'probeuser',
      isOnline: true,
      playerStats: { coins: 1 },
      lastStatsUploadAt: new Date().toISOString(),
    };
    store.flushSessionImmediate('probeuser', row, { full: true });
    store.flushSessionImmediate('probeuser', row);
    assert.equal(accountCalls, 1);
    assert.equal(presenceCalls, 1);
  } finally {
    sharded.useShardedStorage = origUse;
    sharded.flushAccountSync = origFlushAccount;
    sharded.flushPresenceHeartbeatSync = origPresence;
  }
});
