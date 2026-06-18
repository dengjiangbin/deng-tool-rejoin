'use strict';

/**
 * P0 regression suite (2026-06-18) — source-of-truth report identity + binary
 * online/offline state machine.
 *
 * Locks in the contract that online/offline truth and the "last real activity"
 * timer advance ONLY from FRESH, UNIQUE Roblox-side reports — never from a
 * duplicate/replay/cached report, frontend poll, browser refresh/login, backend
 * precompute write, or read-API serve time. Also locks the grace state machine
 * (soft 150s / hard 195s) so a single missed report / slow poll / 502 cannot
 * false-red an in-game account, while a real AFK/278 disconnect goes red after
 * the hard threshold and the offline timer then increases forever.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const identity = require('../src/trackerReportIdentity');
const { deriveAccountPresenceStatus } = require('../src/trackerAccountPresence');
const readApp = require('../src/trackerReadApp');

const ISO = (ms) => new Date(ms).toISOString();

function applyStatus(session, body, nowMs) {
  const r = identity.applyReport('status', session, body, nowMs);
  return { session: { ...session, ...r.updates }, result: r };
}

// 1. Fresh statusReportId makes username green and resets status age.
test('1. fresh statusReportId → green + age resets to ~0', () => {
  const now = Date.parse('2026-06-18T12:00:00.000Z');
  let session = {};
  const out = applyStatus(session, { sessionId: 's1', statusSeq: 1, statusReportId: 's1:1' }, now);
  assert.equal(out.result.fresh, true);
  assert.equal(out.session.lastRealRobloxStatusAt, ISO(now));
  assert.equal(out.session.statusRevision, 1);
  const st = identity.evaluateStatusState(out.session, now);
  assert.equal(st.online, true);
  assert.equal(st.statusColor, 'green');
  assert.equal(st.statusAgeSeconds, 0);
});

// 2. Duplicate same statusReportId does not reset age.
test('2. duplicate statusReportId does NOT advance lastReal / revision', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  let { session } = applyStatus({}, { sessionId: 's1', statusSeq: 5, statusReportId: 's1:5' }, t0);
  const t1 = t0 + 40_000;
  const dup = applyStatus(session, { sessionId: 's1', statusSeq: 5, statusReportId: 's1:5' }, t1);
  assert.equal(dup.result.fresh, false);
  assert.equal(dup.result.reason, 'duplicate_report_id');
  assert.equal(dup.session.lastRealRobloxStatusAt, ISO(t0), 'age anchor must not move');
  assert.equal(dup.session.statusRevision, 1, 'revision must not increment on a duplicate');
});

// 3. Same statusSeq replay does not reset age.
test('3. same statusSeq replay does NOT reset age', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  let { session } = applyStatus({}, { sessionId: 's1', statusSeq: 10 }, t0);
  const replay = applyStatus(session, { sessionId: 's1', statusSeq: 10 }, t0 + 90_000);
  assert.equal(replay.result.fresh, false);
  assert.equal(replay.session.lastRealRobloxStatusAt, ISO(t0));
  // a LOWER seq (out-of-order replay) is also rejected
  const lower = applyStatus(session, { sessionId: 's1', statusSeq: 7 }, t0 + 120_000);
  assert.equal(lower.result.fresh, false);
  assert.equal(lower.result.reason, 'stale_or_replayed_seq');
});

// 4. New sessionId with seq reset accepted only if session is newer/valid.
test('4. new sessionId (seq reset) accepted when newer; stale-replay rejected', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  let { session } = applyStatus({}, { sessionId: 'old', statusSeq: 900 }, t0);
  // New join, seq resets to 1, captured NOW (newer) → accepted.
  const t1 = t0 + 200_000;
  const rejoin = applyStatus(session, { sessionId: 'new', statusSeq: 1, capturedAt: ISO(t1) }, t1);
  assert.equal(rejoin.result.fresh, true);
  assert.equal(rejoin.result.reason, 'new_session');
  assert.equal(rejoin.session.statusSessionId, 'new');
  assert.equal(rejoin.session.statusSeq, 1);
  // A different session whose capture is OLDER than what we already trusted is a
  // stale replay and must be rejected.
  const stale = applyStatus(rejoin.session, { sessionId: 'ghost', statusSeq: 1, capturedAt: ISO(t0 - 60_000) }, t1 + 1000);
  assert.equal(stale.result.fresh, false);
  assert.equal(stale.result.reason, 'stale_session_replay');
});

// 5. No fresh status for hard threshold turns username red.
test('5. no fresh status past hard threshold (195s) → red', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  const session = { lastRealRobloxStatusAt: ISO(t0) };
  const st = identity.evaluateStatusState(session, t0 + 200_000);
  assert.equal(st.online, false);
  assert.equal(st.statusColor, 'red');
  assert.equal(st.statusDecisionReason, 'hard_offline_timeout');
});

// 6. One missed 60s report does not flicker red.
test('6. one (and two) missed 60s reports stays green (no flicker)', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  const session = { lastRealRobloxStatusAt: ISO(t0) };
  assert.equal(identity.evaluateStatusState(session, t0 + 120_000).online, true, 'one miss → green');
  assert.equal(identity.evaluateStatusState(session, t0 + 180_000).online, true, 'two misses → still green');
});

// 7. 502/503/read failure does not instantly mark offline or wipe data.
test('7. transient 5xx within grace stays online; offline read preserves data', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  const session = {
    lastRealRobloxStatusAt: ISO(t0),
    isOnline: true,
    lastFailureReason: 'server_502_upload_retrying',
    lastUploadStatusCodeReturned: 502,
  };
  const p = deriveAccountPresenceStatus(session, undefined, t0 + 30_000);
  assert.equal(p.accountPresenceLive, true, 'a single 502 must not flip red');
  // read-app contract: offline-but-has-data preserves (never wipes)
  const c = readApp._buildPresenceContract({
    presenceInput: { lastRealRobloxStatusAt: ISO(t0), isOnline: true },
    hasRenderableData: true,
  }, t0 + 300_000);
  assert.equal(c.isOnline, false);
  assert.equal(c.preservedDataReason, 'offline_preserve_last_known');
  assert.equal(c.hasRenderableData, true);
});

// 8. AFK/disconnect simulation: reports stop → red after grace; timer keeps increasing.
test('8. AFK/278 disconnect: reports stop → red after grace, age increases forever', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  const session = { lastRealRobloxStatusAt: ISO(t0) };
  assert.equal(identity.evaluateStatusState(session, t0 + 100_000).online, true);
  const red = identity.evaluateStatusState(session, t0 + 220_000);
  assert.equal(red.online, false);
  const later = identity.evaluateStatusState(session, t0 + 3_600_000);
  assert.equal(later.online, false);
  assert.equal(later.statusAgeSeconds, 3600, 'offline age keeps increasing from last real report');
  assert.ok(later.missedStatusReports >= 59);
});

// 9. Human opening /tracker does not reset timer.
test('9. repeated reads (human opening /tracker) never reset the age', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  const input = { presenceInput: { lastRealRobloxStatusAt: ISO(t0), isOnline: true } };
  const a = readApp._buildPresenceContract(input, t0 + 30_000);
  const b = readApp._buildPresenceContract(input, t0 + 90_000);
  assert.equal(a.lastRealStatusAt, ISO(t0));
  assert.equal(b.lastRealStatusAt, ISO(t0), 'anchor identical across reads');
  assert.equal(a.statusAgeSeconds, 30);
  assert.equal(b.statusAgeSeconds, 90, 'age grows only with wall-clock, not reset by the read');
});

// 10. Backend precompute write without new Roblox report does not reset timer.
test('10. precompute touch without a new report does not reset lastReal/revision', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  let { session } = applyStatus({}, { sessionId: 's1', statusSeq: 1 }, t0);
  // Simulate a worker/precompute pass that re-runs identity with the SAME body.
  const touch = applyStatus(session, { sessionId: 's1', statusSeq: 1 }, t0 + 50_000);
  assert.equal(touch.session.lastRealRobloxStatusAt, ISO(t0));
  assert.equal(touch.session.statusRevision, 1);
});

// 11 & 12. Offline preserves leaderstats + inventory (identity-gated lanes never wipe).
test('11/12. stale leaderstats/inventory reports preserve last-known values', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  let session = {
    lastValidLeaderstats: { coins: 123, totalCaught: 45, rarestFishChance: '1/1000' },
    playerDataFishItems: [{ name: 'Tuna' }],
    playerDataStoneItems: [{ name: 'Ruby' }],
  };
  // a duplicate leaderstats report must NOT advance and must NOT touch values
  const ls1 = identity.applyReport('leaderstats', session, { leaderstatsUploadSeq: 5 }, t0);
  session = { ...session, ...ls1.updates };
  const ls2 = identity.applyReport('leaderstats', session, { leaderstatsUploadSeq: 5 }, t0 + 60_000);
  assert.equal(ls2.fresh, false, 'duplicate leaderstats seq is stale');
  // values untouched
  assert.equal(session.lastValidLeaderstats.coins, 123);
  assert.equal(session.playerDataFishItems.length, 1);
  assert.equal(session.playerDataStoneItems.length, 1);
});

// 13. Online status lane remains green even if inventory lane is delayed.
test('13. status green even when inventory lane is far stale', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  const session = {
    lastRealRobloxStatusAt: ISO(t0),
    lastRealInventoryAt: ISO(t0 - 10 * 60_000),
  };
  const st = identity.evaluateStatusState(session, t0 + 30_000);
  assert.equal(st.online, true, 'status uses only lastRealRobloxStatusAt');
});

// 14. Inventory lane stale does not affect status lane.
test('14. a stale inventory report does not change any status field', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  let { session } = applyStatus({}, { sessionId: 's1', statusSeq: 3 }, t0);
  const beforeStatus = session.lastRealRobloxStatusAt;
  const inv = identity.applyReport('inventory', session, { inventorySeq: 1, inventoryReportId: 'i:1' }, t0 + 5_000);
  session = { ...session, ...inv.updates };
  assert.equal(session.lastRealRobloxStatusAt, beforeStatus, 'status anchor untouched by inventory lane');
  assert.equal(session.statusRevision, 1);
});

// 15. Leaderstats lane stale does not affect status lane.
test('15. a leaderstats report does not change any status field', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  let { session } = applyStatus({}, { sessionId: 's1', statusSeq: 3 }, t0);
  const ls = identity.applyReport('leaderstats', session, { leaderstatsUploadSeq: 99 }, t0 + 5_000);
  session = { ...session, ...ls.updates };
  assert.equal(session.lastRealRobloxStatusAt, ISO(t0));
  assert.equal(session.statusSeq, 3, 'status seq owned only by the status lane');
  assert.equal(session.leaderstatsSeq, 99);
});

// 16. Singleton worker guard still present.
test('16. singleton worker guard internals still exported', () => {
  const worker = require('../src/trackerWorkerApp');
  assert.equal(typeof worker._internals.claimSingleton, 'function');
  assert.equal(typeof worker._internals.singletonSuperseded, 'function');
});

// 17. Orphan-loop guard: identity truth flows through the worker presence record
//     so a precompute pass cannot invent freshness (it only copies lastReal*).
test('17. worker presence fields carry identity truth (no invented freshness)', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'trackerWorkerApp.js'), 'utf8');
  assert.match(src, /lastRealRobloxStatusAt/, 'worker must pass through lastRealRobloxStatusAt');
  assert.match(src, /statusRevision/, 'worker must pass through statusRevision');
});

// 18. Presence decoupling: status / leaderstats / inventory are independent lanes.
test('18. presence decoupling — three independent lanes with own revisions', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  let session = {};
  session = { ...session, ...identity.applyReport('status', session, { sessionId: 's', statusSeq: 1 }, t0).updates };
  session = { ...session, ...identity.applyReport('leaderstats', session, { leaderstatsUploadSeq: 1 }, t0).updates };
  session = { ...session, ...identity.applyReport('inventory', session, { inventorySeq: 1 }, t0).updates };
  assert.equal(session.statusRevision, 1);
  assert.equal(session.leaderstatsRevision, 1);
  assert.equal(session.inventoryRevision, 1);
  assert.ok(session.lastRealRobloxStatusAt && session.lastRealLeaderstatsAt && session.lastRealInventoryAt);
});

// Reinforcement: a fresh ONLINE inventory/leaderstats report advances status
// truth (so a lagging heartbeat cannot false-red an in-game account), but a
// stale/replayed one cannot.
test('reinforce: fresh online inventory advances status truth; stale does not', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  let session = {};
  // online inventory upload, fresh → reinforces status truth
  const inv = identity.applyReport('inventory', session, { uploadSeq: 10 }, t0);
  session = { ...session, ...inv.updates };
  assert.equal(inv.fresh, true);
  const adv = identity.advanceStatusTruth(session, inv.capturedAtMs, t0, 'inventory');
  session = { ...session, ...adv };
  assert.equal(session.lastRealRobloxStatusAt, ISO(t0));
  assert.equal(session.statusRevision, 1);
  assert.equal(identity.evaluateStatusState(session, t0 + 30_000).online, true);
  // a duplicate inventory (stale identity) must NOT be treated as fresh → caller
  // will not reinforce, so status truth stays put.
  const dup = identity.applyReport('inventory', session, { uploadSeq: 10 }, t0 + 60_000);
  assert.equal(dup.fresh, false);
});

// Backward-compat: rows with no identity yet fall back to legacy presence and
// auto-migrate on the next real report (no mass false-red on deploy).
test('compat: legacy row (no identity) still derives presence from lastAccountSeenAt', () => {
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  const legacy = { isOnline: true, lastAccountSeenAt: ISO(t0), trackerBuild: 'UPLOAD_HTML_530_GATEWAY_DIAG_2026_06_15' };
  const p = deriveAccountPresenceStatus(legacy, undefined, t0 + 30_000);
  assert.equal(p.accountPresenceLive, true);
  assert.equal(identity.hasRealStatusIdentity(legacy), false);
});

// Worker presence-sweep fix (root cause of "online in-game username shows red"):
// a STATUS-ONLY heartbeat advances the status identity but leaves the heavy
// inventory body byte-stable, so the worker's content dirty-signature (sourceSig)
// is UNCHANGED. Without a decoupled sweep the read API would only refresh
// presence on the slow 120s staleness backstop. This locks in that (a) sourceSig
// ignores the heartbeat, and (b) the tiny presence_json DOES change, so the
// per-tick sweep detects it and republishes fresh online/age within one sweep.
test('worker: status-only heartbeat changes presence_json but NOT the heavy sourceSig', () => {
  const worker = require('../src/trackerWorkerApp');
  const { buildPresenceJson, sourceSig, WORKER_PRESENCE_FIELDS } = worker._internals;
  assert.ok(typeof buildPresenceJson === 'function');
  assert.ok(typeof sourceSig === 'function');
  // identity fields must survive into the worker presence record
  for (const f of ['lastRealRobloxStatusAt', 'statusRevision', 'statusReportId', 'statusSeq']) {
    assert.ok(WORKER_PRESENCE_FIELDS.includes(f), `WORKER_PRESENCE_FIELDS missing ${f}`);
  }
  const t0 = Date.parse('2026-06-18T12:00:00.000Z');
  // Same inventory/leaderstats content; only the status heartbeat advanced.
  const before = {
    isOnline: true,
    playerDataFishItems: [{ n: 1 }], playerDataStoneItems: [], playerDataTotemItems: [],
    lastInventoryAt: ISO(t0 - 600_000), lastStatsUploadAt: ISO(t0 - 600_000),
    lastRealRobloxStatusAt: ISO(t0), statusRevision: 5, statusReportId: 'acct:x:5', statusSeq: 5,
  };
  const after = {
    ...before,
    lastRealRobloxStatusAt: ISO(t0 + 60_000), statusRevision: 6, statusReportId: 'acct:x:6', statusSeq: 6,
  };
  // The heavy rebuild signal must be identical (no wasteful re-enrichment)…
  assert.equal(sourceSig(after), sourceSig(before));
  // …but the tiny presence record MUST differ, so the sweep republishes it.
  assert.notEqual(buildPresenceJson(after), buildPresenceJson(before));
  const parsed = JSON.parse(buildPresenceJson(after));
  assert.equal(parsed.statusRevision, 6);
  assert.equal(parsed.lastRealRobloxStatusAt, ISO(t0 + 60_000));
});

// Worker liveTrackDB staleness fix (2nd root cause of "online shows red"):
// account shards are overwritten on EVERY upload WITHOUT touching index.json, so
// the read-only worker — which gated reloads on index.json mtime — kept serving a
// stale in-memory row (hence stale presence/age) for an actively-online account
// for minutes. This locks in that reloadChangedAccounts now detects a per-shard
// mtime bump even when index.json is byte-identical, AND that the freshness guard
// never clobbers a strictly-newer in-memory row with an older shard.
test('sharded reload: picks up per-shard update with UNCHANGED index.json + freshness guard', async () => {
  const os = require('node:os');
  const fs = require('node:fs');
  const path = require('node:path');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'deng-shard-'));
  const prevRoot = process.env.FISHIT_LIVE_SESSIONS_DIR;
  const prevLegacy = process.env.FISHIT_LIVE_SESSIONS_PATH;
  const prevSharded = process.env.FISHIT_SESSION_SHARDED;
  process.env.FISHIT_LIVE_SESSIONS_DIR = root;
  delete process.env.FISHIT_LIVE_SESSIONS_PATH; // ensure sharded mode
  process.env.FISHIT_SESSION_SHARDED = '1';
  // Fresh module instances bound to this temp root.
  delete require.cache[require.resolve('../src/fishitSessionStoreSharded')];
  delete require.cache[require.resolve('../src/fishitSessionStore')];
  const store = require('../src/fishitSessionStore');
  const sharded = require('../src/fishitSessionStoreSharded');
  try {
    if (!sharded.useShardedStorage()) { return; } // env override unsupported here; skip rather than false-pass

    const t0 = Date.parse('2026-06-18T12:00:00.000Z');
    // 1) Persist an online account and flush to its shard.
    const writer = {};
    store.saveSession('aimer', {
      username: 'Aimer', userId: 7,
      isOnline: true,
      lastUploadReceivedAt: ISO(t0), lastAccountSeenAt: ISO(t0),
      lastRealRobloxStatusAt: ISO(t0), statusRevision: 1, statusReportId: 'sess1:1', statusSeq: 1,
    }, writer);
    await sharded.flushDirtyAccountsAsync({ priority: true });

    // 2) A fresh reader (worker) hydrates from disk and records the shard mtime.
    const reader = {};
    store._invalidateReloadCursorForTests();
    sharded.reloadChangedAccounts(reader, store.sanitiseSession);
    assert.ok(reader.aimer, 'reader hydrated account from shard');
    assert.equal(reader.aimer.statusRevision, 1);

    // 3) New Roblox status report overwrites ONLY the account shard (index.json set
    //    is unchanged — same single account). Write the shard directly to guarantee
    //    index.json is untouched, then bump mtime to a strictly later value.
    const shardFile = path.join(root, 'accounts', 'aimer.json');
    assert.ok(fs.existsSync(shardFile), 'shard file exists on disk');
    const raw = JSON.parse(fs.readFileSync(shardFile, 'utf8'));
    raw.lastUploadReceivedAt = ISO(t0 + 60_000);
    raw.lastAccountSeenAt = ISO(t0 + 60_000);
    raw.lastRealRobloxStatusAt = ISO(t0 + 60_000);
    raw.statusRevision = 2; raw.statusReportId = 'sess1:2'; raw.statusSeq = 2;
    fs.writeFileSync(shardFile, JSON.stringify(raw), 'utf8');
    const future = (Date.now() + 5_000) / 1000;
    fs.utimesSync(shardFile, future, future); // guarantee mtime advances vs cached

    // 4) Reader reloads again — must pick up the per-shard bump WITHOUT any
    //    index.json change.
    const res = sharded.reloadChangedAccounts(reader, store.sanitiseSession);
    assert.equal(res.merged, 1, 'per-shard mtime bump detected without index change');
    assert.equal(reader.aimer.statusRevision, 2);
    assert.equal(reader.aimer.lastRealRobloxStatusAt, ISO(t0 + 60_000));

    // 5) Freshness guard: a reload must NOT clobber a strictly-newer in-memory row
    //    (protects the ingest's just-received upload from an older shard read).
    reader.aimer.lastUploadReceivedAt = ISO(t0 + 600_000);
    reader.aimer.lastAccountSeenAt = ISO(t0 + 600_000);
    reader.aimer.lastRealRobloxStatusAt = ISO(t0 + 600_000);
    reader.aimer.statusRevision = 99;
    // Touch the (older) shard's mtime so the scan re-reads it.
    const future2 = (Date.now() + 10_000) / 1000;
    fs.utimesSync(shardFile, future2, future2);
    sharded.reloadChangedAccounts(reader, store.sanitiseSession);
    assert.equal(reader.aimer.statusRevision, 99, 'newer in-memory row NOT clobbered by older shard');
  } finally {
    if (prevRoot === undefined) delete process.env.FISHIT_LIVE_SESSIONS_DIR; else process.env.FISHIT_LIVE_SESSIONS_DIR = prevRoot;
    if (prevLegacy === undefined) delete process.env.FISHIT_LIVE_SESSIONS_PATH; else process.env.FISHIT_LIVE_SESSIONS_PATH = prevLegacy;
    if (prevSharded === undefined) delete process.env.FISHIT_SESSION_SHARDED; else process.env.FISHIT_SESSION_SHARDED = prevSharded;
    try { fs.rmSync(root, { recursive: true, force: true }); } catch (_) { /* temp cleanup */ }
  }
});
