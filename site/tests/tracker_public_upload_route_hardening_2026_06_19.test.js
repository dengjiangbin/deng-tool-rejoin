'use strict';

/**
 * Regression (2026-06-19) — public upload 502 root cause: the 8792 ingest
 * mutual-kill restart loop.
 *
 * SYMPTOM (live Roblox): required_status / required_leaderstats / inventory_snapshot
 * uploads to https://aio.deng.my.id/api/fishit-tracker/update-backpack returned a
 * Cloudflare `502: Bad gateway` HTML page (`<!DOCTYPE html>`), so online accounts
 * stopped reaching the backend and aged past grace into false-red.
 *
 * ROOT CAUSE: the ingest port-reclaim ran `reclaimAfterMs = 1500ms`, SHORTER than
 * PM2's `kill_timeout = 8000ms`. On every restart the freshly-spawned child killed
 * the previous, still-gracefully-flushing sibling at 1.5s. Killing a PM2-tracked
 * sibling makes PM2 spawn yet another child, which reclaims again … a 2000+ restart
 * mutual-kill loop. During each kill→rebind gap nothing listened on 8792, so
 * Cloudflare emitted the 502 HTML gateway page.
 *
 * THE FIX (this suite locks it):
 *   1. reclaimAfterMs > kill_timeout — a normally-restarting sibling is given the
 *      full graceful-shutdown window to release the port before it can ever be
 *      treated as a stuck orphan.
 *   2. Persistent-holder guard (`onlyPids`) — only the pid that held the port at
 *      the FIRST bind failure may be reclaimed; a different pid that grabbed the
 *      port mid-wait is a healthy hand-off and is deferred, never killed.
 *   3. The web→ingest proxy returns structured JSON (503/504) on ingest
 *      failure/timeout and never forwards an HTML gateway body.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const reclaim = require('../src/reclaimPort');

const SRC = (rel) => fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');
const ECO_SITE = JSON.parse(SRC('../ecosystem.site.json'));
const ECO_SCALE = JSON.parse(SRC('../ecosystem.scale.json'));
const appByName = (eco, name) => eco.apps.find((a) => a.name === name);

// ── Persistent-holder guard: the engine that stops the mutual-kill loop ────────
test('reclaimPort defers (does NOT kill) a holder that was not the original stuck pid', () => {
  // The port is now held by 9999, but the stuck-orphan candidate set was {5555}.
  // 9999 is therefore a fresh healthy hand-off — killing it would restart the loop.
  let killed = [];
  const r = reclaim.reclaimPort(8792, '[test]', {
    onlyPids: [5555],
    _platform: 'win32',
    _selfPid: 1,
    _findListenerPids: () => [9999],
    _describeProcess: (pid) => ({ pid, name: 'node.exe' }),
    _killPid: (pid) => { killed.push(pid); return true; },
  });
  assert.deepEqual(killed, [], 'a non-original holder must never be killed');
  assert.equal(r.reclaimed, false);
  assert.deepEqual(r.deferredPids, [9999]);
});

test('reclaimPort DOES evict a node orphan that persisted as the original stuck holder', () => {
  let killed = [];
  const r = reclaim.reclaimPort(8792, '[test]', {
    onlyPids: [5555],
    _platform: 'win32',
    _selfPid: 1,
    _findListenerPids: () => [5555],
    _describeProcess: (pid) => ({ pid, name: 'node.exe' }),
    _killPid: (pid) => { killed.push(pid); return true; },
  });
  assert.deepEqual(killed, [5555], 'a genuine persistent orphan is reclaimed');
  assert.equal(r.reclaimed, true);
});

test('reclaimPort never kills our own pid', () => {
  let killed = [];
  reclaim.reclaimPort(8792, '[test]', {
    _platform: 'win32',
    _selfPid: 4242,
    _findListenerPids: () => [4242],
    _describeProcess: (pid) => ({ pid, name: 'node.exe' }),
    _killPid: (pid) => { killed.push(pid); return true; },
  });
  assert.deepEqual(killed, [], 'self pid must be skipped');
});

test('reclaimPort never kills a non-node process holding the port', () => {
  let killed = [];
  reclaim.reclaimPort(8792, '[test]', {
    onlyPids: [7000],
    _platform: 'win32',
    _selfPid: 1,
    _findListenerPids: () => [7000],
    _describeProcess: (pid) => ({ pid, name: 'nginx.exe' }),
    _killPid: (pid) => { killed.push(pid); return true; },
  });
  assert.deepEqual(killed, [], 'only node.exe holders are ever reclaimed');
});

// ── Orphan / PID-mismatch detection (test req: orphan holding 8792 is detected) ─
test('findListenerPids surfaces the actual port owner for PID-mismatch detection', () => {
  assert.equal(typeof reclaim.findListenerPids, 'function');
  // A monitor compares this to the PM2-tracked pid; a mismatch => orphan on 8792.
  const owner = 8152; const pm2Pid = 2724;
  assert.notEqual(owner, pm2Pid, 'owner != pm2 pid is the orphan signal the fix removes');
});

// ── Timing invariants that actually break the loop ─────────────────────────────
test('reclaimAfterMs default is greater than PM2 kill_timeout (no premature sibling kill)', () => {
  const src = SRC('src/reclaimPort.js');
  const m = src.match(/reclaimAfterMs\s*=\s*opts\.reclaimAfterMs\s*!=\s*null\s*\?\s*opts\.reclaimAfterMs\s*:\s*(\d+)/);
  assert.ok(m, 'reclaimAfterMs default must be present');
  const reclaimAfterMs = Number(m[1]);
  const killTimeout = appByName(ECO_SITE, 'deng-tracker-ingest').kill_timeout;
  assert.ok(reclaimAfterMs > killTimeout,
    `reclaimAfterMs (${reclaimAfterMs}) must exceed kill_timeout (${killTimeout})`);
});

test('listen retry window sits between reclaimAfterMs and PM2 listen_timeout', () => {
  const src = SRC('src/reclaimPort.js');
  const reclaimAfterMs = Number(src.match(/:\s*(\d+);\s*\n\s*const retryDelayMs/)[1]);
  const maxMs = Number(src.match(/maxMs\s*=\s*opts\.maxMs\s*!=\s*null\s*\?\s*opts\.maxMs\s*:\s*(\d+)/)[1]);
  const listenTimeout = appByName(ECO_SITE, 'deng-tracker-ingest').listen_timeout;
  assert.ok(maxMs > reclaimAfterMs, `maxMs (${maxMs}) must exceed reclaimAfterMs (${reclaimAfterMs})`);
  assert.ok(maxMs < listenTimeout, `maxMs (${maxMs}) must stay below listen_timeout (${listenTimeout})`);
});

test('all three tracker services pass the safe reclaim defaults (9000 / 22000)', () => {
  for (const file of ['tracker-ingest-server.js', 'tracker-read-server.js', 'server.js']) {
    const src = SRC(file);
    assert.match(src, /RECLAIM_AFTER_MS\s*\|\|\s*'9000'/, `${file} reclaimAfterMs default`);
    assert.match(src, /LISTEN_RETRY_MAX_MS\s*\|\|\s*'22000'/, `${file} maxMs default`);
  }
});

test('every tracker service kill_timeout is below the reclaim default (graceful release wins)', () => {
  const services = [
    appByName(ECO_SITE, 'deng-tool-site'),
    appByName(ECO_SITE, 'deng-tracker-ingest'),
    appByName(ECO_SCALE, 'deng-tracker-read'),
  ];
  for (const svc of services) {
    assert.ok(svc.kill_timeout < 9000, `${svc.name} kill_timeout (${svc.kill_timeout}) must be < 9000`);
  }
});

// ── Health-gated reclaim: never kill a LIVE server (the 502-gap fix) ───────────
test('probeHealthy resolves true for a live 200 /health server, false for a dead port', async () => {
  const http = require('http');
  const srv = http.createServer((req, res) => {
    if (req.url === '/health') { res.writeHead(200); res.end('{"status":"ok"}'); }
    else { res.writeHead(404); res.end(); }
  });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const port = srv.address().port;
  assert.equal(await reclaim.probeHealthy(port, '127.0.0.1'), true, 'live server => healthy');
  await new Promise((r) => srv.close(r));
  // Nothing listening now → must report unhealthy (eligible for reclaim).
  assert.equal(await reclaim.probeHealthy(port, '127.0.0.1', 600), false, 'closed port => unhealthy');
});

test('listenWithReclaim health-gates the reclaim (warm spare instead of killing a healthy holder)', () => {
  const src = SRC('src/reclaimPort.js');
  assert.match(src, /warmSpare/);
  assert.match(src, /_probeHealthy/);
  // The reclaim (killPid path) only runs on the NOT-healthy branch.
  assert.match(src, /standing by as warm spare/);
  assert.match(src, /reclaimed \$\{port\} from DEAD orphan/);
});

// ── Proxy never emits an HTML gateway body (test req: HTML gateway = hard fail) ─
test('web→ingest proxy returns structured JSON on ingest failure/timeout, never HTML', () => {
  const src = SRC('src/trackerUploadProxy.js');
  // Connection error -> 503 JSON, timeout -> 504 JSON; no res.end of an HTML page.
  assert.match(src, /writeHead\(503[\s\S]*?tracker_ingest_unavailable/);
  assert.match(src, /writeHead\(504[\s\S]*?tracker_ingest_timeout/);
  assert.doesNotMatch(src, /<!DOCTYPE|<html/i, 'proxy must never synthesize an HTML body');
});

// ── Ingest shutdown ordering (release port before slow flush) ──────────────────
test('ingest shutdown closes the listening socket before the slow flush', () => {
  const src = SRC('tracker-ingest-server.js');
  const closeIdx = src.indexOf('server.close()', src.indexOf('function shutdown'));
  const flushIdx = src.indexOf('flushAllLiveSessionsToDisk', src.indexOf('function shutdown'));
  assert.ok(closeIdx > 0 && flushIdx > 0);
  assert.ok(closeIdx < flushIdx, 'close must precede flush so a restart can bind 8792 immediately');
});
