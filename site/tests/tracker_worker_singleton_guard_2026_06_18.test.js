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

// ── Legacy recency rule (locks WITHOUT a heartbeat field) ──────────────────
// Back-compat: a lock written by an old worker build (no hbAt) still resolves by
// the startMs/pid recency rule so a rolling upgrade can never deadlock.
test('legacy lock (no hbAt): strictly-newer startMs supersedes us', () => {
  writeLock({ startMs: G.MY_START_MS + 5000, pid: G.MY_PID + 1, claimedAt: 'x' });
  assert.equal(G.singletonSuperseded(), true);
});

test('legacy lock (no hbAt): older startMs does NOT supersede us', () => {
  writeLock({ startMs: G.MY_START_MS - 5000, pid: G.MY_PID + 1, claimedAt: 'x' });
  assert.equal(G.singletonSuperseded(), false);
});

test('legacy lock (no hbAt): same startMs uses higher pid as deterministic tiebreaker', () => {
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

// ── Liveness/heartbeat rule (the park-not-exit anti-leapfrog core) ─────────
test('liveness: a DEAD owner (impossible pid) never supersedes us, even with newer startMs+fresh hb', () => {
  // pid 0x7fffffff effectively never exists → not a live owner → we may take over.
  writeLock({ startMs: G.MY_START_MS + 9000, pid: 0x7fffffff, token: 'other.deadpid', hbAt: Date.now() });
  assert.equal(G.singletonSuperseded(), false, 'a dead owner must not park/kill the live worker');
});

test('liveness: a STALE owner (fresh pid but old heartbeat) never supersedes us', () => {
  writeLock({ startMs: G.MY_START_MS + 9000, pid: process.pid, token: 'other.stalehb', hbAt: Date.now() - (G.SINGLETON_HB_STALE_MS + 5000) });
  assert.equal(G.singletonSuperseded(), false, 'a stale owner must be takeover-eligible');
});

test('liveness: a LIVE owner (alive pid + fresh heartbeat + different token) supersedes us', () => {
  writeLock({ startMs: G.MY_START_MS + 9000, pid: process.pid, token: 'other.livehb', hbAt: Date.now() });
  assert.equal(G.singletonSuperseded(), true, 'yield only to a verifiably live, fresh owner');
});

test('our own token (even if older) is never superseded', () => {
  writeLock({ startMs: G.MY_START_MS, pid: G.MY_PID, token: G.MY_TOKEN, hbAt: Date.now() });
  assert.equal(G.singletonSuperseded(), false);
});

// ── ensureOwnership: park-not-exit decision ───────────────────────────────
test('ensureOwnership: claims when no lock exists', () => {
  try { fs.unlinkSync(LOCK_PATH); } catch (_) { /* ignore */ }
  assert.equal(G.ensureOwnership(), true);
  const raw = JSON.parse(fs.readFileSync(LOCK_PATH, 'utf8'));
  assert.equal(raw.token, G.MY_TOKEN);
});

test('ensureOwnership: PARKS (returns false) when a live owner holds the lock', () => {
  writeLock({ startMs: G.MY_START_MS + 9000, pid: process.pid, token: 'other.live', hbAt: Date.now() });
  assert.equal(G.ensureOwnership(), false, 'must park, not steal, from a live owner');
});

test('ensureOwnership: TAKES OVER a dead owner and writes our token', () => {
  writeLock({ startMs: G.MY_START_MS + 9000, pid: 0x7fffffff, token: 'other.dead', hbAt: Date.now() });
  assert.equal(G.ensureOwnership(), true);
  const raw = JSON.parse(fs.readFileSync(LOCK_PATH, 'utf8'));
  assert.equal(raw.token, G.MY_TOKEN, 'we became the owner');
});

test('worker source: a superseded worker PURE-PARKS — never self-exits and never kills peers (no PM2 cascade); start is ownership-aware', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'trackerWorkerApp.js'), 'utf8');
  const tickBody = src.slice(src.indexOf('async function tick()'), src.indexOf('function start()'));
  assert.match(tickBody, /ensureOwnership\(\)/, 'tick must check ownership');
  assert.match(tickBody, /parked/, 'tick must implement parking');
  // A non-owner must NEVER self-exit and the worker must NEVER kill another worker
  // process. Either action feeds PM2 autorestart and recreates the leapfrog/cascade
  // that produced the multi-minute dead period. The single live owner always keeps
  // presence fresh; losers park harmlessly (no DB writes) until PM2 reaps them on a
  // clean restart.
  assert.doesNotMatch(tickBody, /process\.exit/, 'a parked worker must NOT exit (no cascade)');
  const wholeSrc = src;
  assert.doesNotMatch(wholeSrc, /process\.kill\([^,]+,\s*['"]SIG(TERM|KILL)['"]\)/, 'worker must never kill another worker (no cascade)');
  const startBody = src.slice(src.indexOf('function start()'), src.indexOf('function stop()'));
  assert.match(startBody, /ensureOwnership\(\)/, 'start must be ownership-aware (claim only if no live owner)');
});
