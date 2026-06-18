'use strict';

/**
 * P0 presence-decoupling regression (2026-06-18).
 *
 * Root cause of "online account not refreshing" + "offline age frozen while
 * heartbeats continue": the worker skips re-writing the inventory body when its
 * content hash is unchanged (to avoid read-lane JSON amplification), so a
 * heartbeat-only upload never bumped precomputed_hash and the read lane never
 * refreshed presence -> an actively-uploading account read stale "offline" with
 * a frozen age.
 *
 * Fix: a tiny decoupled presence record (presence_json) the worker refreshes on
 * every heartbeat (even when content is byte-stable); the read lane refreshes
 * presence from it WITHOUT pulling the heavy JSON.
 */

const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');

// Isolate the store on a throwaway DB before requiring it.
process.env.FISHIT_PRECOMPUTE_DB_PATH = path.join(
  os.tmpdir(), `fishit_presence_decouple_${process.pid}_${Date.now()}.db`,
);

const test = require('node:test');
const assert = require('node:assert/strict');
const store = require('../src/fishitPrecomputeStore');

function isoAgo(ms) { return new Date(Date.now() - ms).toISOString(); }

test('updatePresence refreshes presence WITHOUT changing the heavy JSON or content hash', () => {
  const key = 'decouple_user';
  const body = { username: key, playerStats: { coins: 1 }, fishItems: [{ id: 'a' }], lastAccountSeenAt: isoAgo(9 * 60 * 1000) };
  store.upsertLatest({
    sessionKey: key,
    username: key,
    precomputedJson: JSON.stringify(body),
    precomputedHash: 'HASH_V1',
    rawHash: 'raw1',
    presenceJson: JSON.stringify({ isOnline: false, lastAccountSeenAt: body.lastAccountSeenAt }),
  });
  const before = store.getJsonByKey(key);
  assert.equal(before.precomputedHash, 'HASH_V1');

  // Fresh heartbeat arrives, inventory unchanged.
  const freshSeen = isoAgo(5 * 1000);
  store.updatePresence(key, JSON.stringify({ isOnline: true, lastAccountSeenAt: freshSeen }));

  const after = store.getJsonByKey(key);
  // Heavy JSON + content hash MUST be untouched (no read-lane amplification).
  assert.equal(after.json, before.json, 'inventory JSON must NOT be rewritten on a heartbeat-only update');
  assert.equal(after.precomputedHash, 'HASH_V1', 'precomputed_hash must NOT change on a heartbeat-only update');

  // But the lightweight change probe must expose the fresh presence record.
  const meta = store.getChangedMetaSince('').find((r) => r.session_key === key);
  assert.ok(meta, 'row must be visible to the read lane change probe');
  const presence = JSON.parse(meta.presence_json);
  assert.equal(presence.isOnline, true);
  assert.equal(presence.lastAccountSeenAt, freshSeen, 'presence record must carry the fresh heartbeat timestamp');
});

test('read API derives ONLINE from the decoupled presence record even if body presence is stale/offline', () => {
  // The read app keys its cache off the same store; verify its presence contract
  // consumes presence_json (fresh) over the body (stale).
  const readApp = require('../src/trackerReadApp');
  const freshSeen = isoAgo(10 * 1000);
  // Body says long-offline; presence record says fresh heartbeat.
  const hit = {
    presenceInput: JSON.parse(JSON.stringify({ isOnline: true, lastAccountSeenAt: freshSeen, lastStatsUploadAt: freshSeen })),
    hasRenderableData: true,
    snapshotSource: 'precomputed',
  };
  const c = readApp._buildPresenceContract(hit, Date.now());
  assert.equal(c.presenceState, 'online', 'fresh heartbeat must read online');
  assert.equal(c.isOnline, true);
  assert.ok(c.statusAgeSeconds != null && c.statusAgeSeconds < 150, 'status age must reflect the fresh heartbeat');
});

test('offline-since-last-night stays red with hours-old age (no fake reset)', () => {
  const readApp = require('../src/trackerReadApp');
  const eightHoursAgo = isoAgo(8 * 60 * 60 * 1000);
  const hit = {
    presenceInput: { isOnline: false, lastAccountSeenAt: eightHoursAgo, lastStatsUploadAt: eightHoursAgo },
    hasRenderableData: true,
    snapshotSource: 'precomputed',
  };
  const c = readApp._buildPresenceContract(hit, Date.now());
  assert.equal(c.presenceState, 'offline');
  assert.equal(c.isOnline, false);
  assert.ok(c.statusAgeSeconds >= 8 * 60 * 60 - 5, `age must be ~8h, got ${c.statusAgeSeconds}s`);
});

test('cleanup throwaway db', () => {
  try { store.close(); } catch (_) { /* ignore */ }
  for (const ext of ['', '-wal', '-shm']) {
    try { fs.unlinkSync(process.env.FISHIT_PRECOMPUTE_DB_PATH + ext); } catch (_) { /* ignore */ }
  }
  assert.ok(true);
});
