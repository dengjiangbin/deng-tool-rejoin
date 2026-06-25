'use strict';

/**
 * P0 deploy/infra guard (2026-06-18).
 *
 * The "online account not refreshing" + "offline shows fake 24m" live symptom
 * was caused by an orphan-PID restart loop on 8791/8793: server.close() waited
 * for keep-alive poller sockets to drain, so the dying process kept the port,
 * the PM2-restarted instance lost the bind race and exited, and the slow-dying
 * fork orphaned while still serving STALE old code/bundle. Fix = force-close all
 * connections on shutdown so the port releases instantly.
 *
 * These guards keep that fix (and the build marker) from silently regressing.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function read(rel) {
  return fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');
}

test('read entrypoint force-closes connections on shutdown (prevents orphan-PID loop)', () => {
  const src = read('tracker-read-server.js');
  const sd = src.slice(src.indexOf('function shutdown'));
  assert.match(sd, /closeAllConnections/, 'shutdown must destroy keep-alive sockets to release the port instantly');
  assert.match(sd, /server\.close\(\)/);
});

test('site entrypoint force-closes connections on shutdown', () => {
  const src = read('server.js');
  const sd = src.slice(src.indexOf('function shutdown'));
  assert.match(sd, /closeAllConnections/, 'site shutdown must force-close to avoid orphaning 8791');
});

test('ingest entrypoint force-closes connections on shutdown (prevents 8792 orphan loop)', () => {
  // The ingest holds ~1000+ keep-alive sockets; without closeAllConnections the
  // dying process keeps port 8792 bound, the PM2 restart loses the bind race and
  // orphans, producing the restart loop + Cloudflare 530/502. This guard keeps
  // the fix in place.
  const src = read('tracker-ingest-server.js');
  const sd = src.slice(src.indexOf('function shutdown'));
  assert.match(sd, /closeAllConnections/, 'ingest shutdown must force-close keep-alive sockets to release 8792 instantly');
  assert.match(sd, /server\.close\(\)/);
});

test('frontend carries the build marker (proves loaded bundle is not stale-cached)', () => {
  const src = read('src/inventory/fishit_tracker.source.ejs');
  assert.match(src, /TRACKER_SERVERNOW_TIMER_502_FIX_2026_06_25/);
  assert.match(src, /window\.__TRACKER_BUILD_MARKER\s*=/);
});

test('built bundle in manifest is shipped and contains the marker + presence wiring', () => {
  const manifest = JSON.parse(read('src/inventoryAssetManifest.json'));
  const jsPath = path.join(__dirname, '..', 'public', 'assets', manifest.js);
  assert.ok(fs.existsSync(jsPath), `manifest bundle ${manifest.js} must exist on disk`);
  const js = fs.readFileSync(jsPath, 'utf8');
  assert.match(js, /TRACKER_SERVERNOW_TIMER_502_FIX_2026_06_25/, 'shipped bundle must include the new marker');
  assert.match(js, /X-DENG-Presence-State/, 'shipped bundle must include authoritative presence wiring');
});
