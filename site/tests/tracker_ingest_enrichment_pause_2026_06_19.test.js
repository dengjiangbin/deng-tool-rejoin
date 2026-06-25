'use strict';

/**
 * P0 upload-reliability regression (2026-06-19).
 *
 * Production symptom: Roblox uploads to the direct-ingest origin (8792) returned
 * frequent Cloudflare 530 / 502 and app-side 503/server_busy. Root cause was NOT
 * the upload handler rejecting work — the ingest emitted 0 HTTP 5xx. The ingest
 * event loop was saturated (~5.8s lag, /metrics could not respond), so the
 * Cloudflare tunnel timed out the origin (530/502) and the gate shed work (503).
 *
 * The defect: effectiveEnrichmentMax() floored at 1, so a single CPU-bound
 * enrichment job ran back-to-back forever and the loop never recovered. The fix
 * lets the gate pause enrichment entirely (max -> 0) above a hard lag ceiling so
 * the loop drains uploads and answers the tunnel in time. Display data is
 * persisted on the synchronous fast path before enrichment is scheduled, so the
 * pause never blanks an account.
 *
 * These guards keep that fix from regressing.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const gate = require('../src/trackerConcurrencyGate');
const eventLoop = require('../src/trackerEventLoopMonitor');

function resetAll() {
  gate._resetForTests();
  eventLoop._resetForTests();
}

test('enrichment fully pauses (effectiveMax = 0) under extreme event-loop lag', () => {
  resetAll();
  // Healthy loop: enrichment runs at full concurrency.
  eventLoop._setLagForTests(0);
  const healthyMax = gate.stats().effectiveMax;
  assert.ok(healthyMax >= 1, `healthy effectiveMax should be >=1, got ${healthyMax}`);

  // Extreme lag (>= default pause 2500ms): the loop must yield completely so it
  // can drain uploads and answer the Cloudflare tunnel — this is what kills the
  // 530/502 origin-timeout. The previous floor of 1 is the bug we are guarding.
  eventLoop._setLagForTests(5865); // the exact production lag value observed
  assert.equal(gate.stats().effectiveMax, 0, 'extreme lag must pause enrichment to 0, not floor at 1');

  resetAll();
});

test('under pause, net-new deferred enrichment is shed (not started), display fast-path unaffected', () => {
  resetAll();
  eventLoop._setLagForTests(6000);
  const shedBefore = gate.stats().shedEvents;
  let ran = false;
  gate.scheduleDeferredUploadWork('somenewaccount', () => { ran = true; });
  const s = gate.stats();
  assert.equal(ran, false, 'enrichment must NOT start synchronously while paused');
  assert.ok(s.shedEvents > shedBefore, 'net-new work under extreme lag must increment shedEvents');
  assert.equal(s.active, 0, 'no enrichment job should be active while paused');
  resetAll();
});

test('enrichment resumes at full concurrency once lag recovers', () => {
  resetAll();
  eventLoop._setLagForTests(0);
  assert.ok(gate.stats().effectiveMax >= 1, 'recovered loop must allow enrichment again');
  resetAll();
});

test('fast-lane status/leaderstats uploads bypass the gate entirely (never blocked by lag)', () => {
  resetAll();
  eventLoop._setLagForTests(9000);
  let statusHandled = false;
  let leaderHandled = false;
  const statusReq = { body: { type: 'tracker_status', username: 'a' } };
  const leaderReq = { body: { leaderstatsOnlyUpload: true, username: 'b' } };
  const res = {};
  gate.wrapTrackerUpload('status', () => { statusHandled = true; })(statusReq, res);
  gate.wrapTrackerUpload('leaderstats', () => { leaderHandled = true; })(leaderReq, res);
  assert.equal(statusHandled, true, 'status lane must always run even under extreme lag');
  assert.equal(leaderHandled, true, 'leaderstats lane must always run even under extreme lag');
  resetAll();
});

test('pause threshold is env-tunable and defaults above the shed threshold', () => {
  // Sanity: a pure-lag pause must sit ABOVE the shed threshold so moderate lag
  // still does useful (halved) enrichment instead of stalling prematurely.
  const shed = Number(process.env.TRACKER_EVENT_LOOP_LAG_SHED_MS || 1000);
  const pause = Number(process.env.TRACKER_EVENT_LOOP_LAG_PAUSE_MS || 2500);
  assert.ok(pause > shed, `pause (${pause}) must be greater than shed (${shed})`);
});
