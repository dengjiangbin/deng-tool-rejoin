'use strict';

const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

// Regression coverage for the 2026-06-15 burst-load hardening that eliminated
// Cloudflare 502/503/timeouts under 100-client bursts:
//   1. ingest EADDRINUSE retry (no more 500+ restart loop / orphan PID on 8792)
//   2. debounced/coalesced post-upload owner cache refresh (the event-loop amplifier)
//   3. throttled "no bot DB user" warning (log-flood that amplified lag)

describe('ingest server — EADDRINUSE retry instead of exit loop', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'tracker-ingest-server.js'),
    'utf8',
  );

  test('retries the bind on EADDRINUSE rather than exiting immediately', () => {
    assert.match(source, /EADDRINUSE/);
    assert.match(source, /LISTEN_RETRY_MAX_MS|listenRetryStartedAt/);
    // Ingest now binds via the shared listen-with-reclaim helper (retries the
    // bind AND evicts a genuine stuck orphan) instead of the old inline
    // server.listen(PORT, HOST) that exited without reclaiming — the exit-loop
    // that let an orphan hold 8792 forever and produced Cloudflare 502s.
    assert.match(source, /listenWithReclaim\(server, PORT, HOST/);
  });

  test('releases the listening socket first on shutdown', () => {
    // close() must run before the (slow) flush so a restarted PM2 instance can bind.
    const closeIdx = source.indexOf('server.close()', source.indexOf('function shutdown'));
    const flushIdx = source.indexOf('flushAllLiveSessionsToDisk', source.indexOf('function shutdown'));
    assert.ok(closeIdx > 0 && flushIdx > 0, 'shutdown must close and flush');
    assert.ok(closeIdx < flushIdx, 'server.close must precede the flush');
  });
});

describe('aioDatasetCache — post-upload refresh is debounced/coalesced', () => {
  let cache;
  beforeEach(() => {
    delete require.cache[require.resolve('../src/aioDatasetCache')];
    process.env.AIO_REFRESH_DEBOUNCE_MS = '50';
    cache = require('../src/aioDatasetCache');
    cache._resetForTests();
  });

  test('many uploads for one owner collapse into a single rebuild (latest wins)', async () => {
    let builds = 0;
    let lastTag = null;
    const mk = (tag) => async () => { builds += 1; lastTag = tag; return { tag }; };

    // 25 rapid uploads for the same owner within the debounce window.
    for (let i = 0; i < 25; i += 1) {
      cache.refreshOwnersAfterUpload('owner-1', { dashboard: mk(`v${i}`) });
    }
    assert.strictEqual(builds, 0, 'no synchronous build on the request path');

    await new Promise((r) => setTimeout(r, 140));
    assert.strictEqual(builds, 1, 'burst collapsed into exactly one rebuild');
    assert.strictEqual(lastTag, 'v24', 'latest builder wins');
  });

  test('does not bypass the pending guard with force+immediate per upload', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'aioDatasetCache.js'), 'utf8');
    assert.doesNotMatch(src, /force:\s*true,\s*immediate:\s*true/);
    assert.match(src, /REFRESH_DEBOUNCE_MS/);
  });
});

describe('fishitDb — no-bot-user warning is throttled', () => {
  test('shouldLogNoBotUserWarning throttles repeats per Discord id', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitDb.js'), 'utf8');
    assert.match(src, /shouldLogNoBotUserWarning/);
    assert.match(src, /NO_BOT_USER_WARN_WINDOW_MS/);
  });
});
