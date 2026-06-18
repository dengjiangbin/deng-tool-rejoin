'use strict';

/**
 * P0 root-cause guard (2026-06-18).
 *
 * Live symptom: an ONLINE/uploading account showed no fresh upload and an
 * OFFLINE account showed a frozen ~24-25m age. Root cause = MULTIPLE orphan
 * deng-tracker-worker processes (left behind by PM2 restart/daemon churn) all
 * writing the SAME fishit_precompute.db. An orphan with a frozen in-memory
 * liveTrackDB kept re-stamping presence/age with stale timestamps on its idle
 * refresh, clobbering the fresh worker's correct writes — so every account's
 * presence/age froze at the orphan's boot snapshot regardless of real uploads.
 *
 * Fix = singleton claim token: the NEWEST worker owns the lock; any worker that
 * observes a strictly-newer token self-exits within one tick, so PM2 churn can
 * never leave two workers competing over the precompute DB.
 *
 * These tests lock that behavior in.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const LOCK_PATH = path.join(os.tmpdir(), `deng_worker_singleton_test_${process.pid}.json`);
process.env.TRACKER_WORKER_LOCK_PATH = LOCK_PATH;
process.env.NODE_ENV = 'test';

const worker = require('../src/trackerWorkerApp');
const G = worker._internals;

test.after(() => {
  try { fs.unlinkSync(LOCK_PATH); } catch (_) { /* ignore */ }
});

function writeLock(token) {
  fs.writeFileSync(LOCK_PATH, JSON.stringify(token));
}

test('worker exposes singleton guard internals', () => {
  assert.equal(typeof G.claimSingleton, 'function');
  assert.equal(typeof G.singletonSuperseded, 'function');
  assert.equal(typeof G.MY_START_MS, 'number');
  assert.equal(typeof G.MY_PID, 'number');
});

test('claimSingleton writes our own token to the lock file', () => {
  assert.equal(G.claimSingleton(), true);
  const raw = JSON.parse(fs.readFileSync(LOCK_PATH, 'utf8'));
  assert.equal(raw.pid, G.MY_PID);
  assert.equal(raw.startMs, G.MY_START_MS);
});

test('the holder of the lock is NOT superseded by its own token', () => {
  G.claimSingleton();
  assert.equal(G.singletonSuperseded(), false);
});

test('a strictly-newer worker (later startMs) supersedes us', () => {
  writeLock({ startMs: G.MY_START_MS + 5000, pid: G.MY_PID + 1, claimedAt: 'x' });
  assert.equal(G.singletonSuperseded(), true);
});

test('an older worker (earlier startMs) does NOT supersede us', () => {
  writeLock({ startMs: G.MY_START_MS - 5000, pid: G.MY_PID + 1, claimedAt: 'x' });
  assert.equal(G.singletonSuperseded(), false);
});

test('same startMs uses higher pid as deterministic tiebreaker', () => {
  writeLock({ startMs: G.MY_START_MS, pid: G.MY_PID + 1, claimedAt: 'x' });
  assert.equal(G.singletonSuperseded(), true, 'higher pid wins the tie');
  writeLock({ startMs: G.MY_START_MS, pid: Math.max(1, G.MY_PID - 1), claimedAt: 'x' });
  assert.equal(G.singletonSuperseded(), false, 'lower pid yields to us');
});

test('a missing/corrupt lock file is treated as not-superseded (fail-open, never blocks the sole worker)', () => {
  try { fs.unlinkSync(LOCK_PATH); } catch (_) { /* ignore */ }
  assert.equal(G.singletonSuperseded(), false);
  fs.writeFileSync(LOCK_PATH, '{ this is not json');
  assert.equal(G.singletonSuperseded(), false);
});

test('worker source enforces the guard on every tick and on start', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'trackerWorkerApp.js'), 'utf8');
  const tickBody = src.slice(src.indexOf('async function tick()'), src.indexOf('function start()'));
  assert.match(tickBody, /singletonSuperseded\(\)/, 'tick must check supersession');
  assert.match(tickBody, /process\.exit\(0\)/, 'a superseded worker must exit cleanly');
  const startBody = src.slice(src.indexOf('function start()'));
  assert.match(startBody, /claimSingleton\(\)/, 'start must claim the singleton lock');
});
