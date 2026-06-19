'use strict';

// P0 2026-06-19 — sharded disk reload must not keep stale high-seq identity from
// a prior Roblox session when disk carries a newer real lane timestamp.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const sharded = require('../src/fishitSessionStoreSharded');

describe('sharded preserveMonotonicLanes — new Roblox session wins over stale high seq', () => {
  test('new session with lower seq but newer lastRealRobloxStatusAt replaces stale in-memory bundle', () => {
    const existing = {
      statusSessionId: 'old-session-aaa',
      statusSeq: 69,
      statusRevision: 485,
      statusReportId: 'old-session-aaa:69',
      lastRealRobloxStatusAt: '2026-06-19T14:20:38.216Z',
    };
    const disk = {
      statusSessionId: 'new-session-bbb',
      statusSeq: 9,
      statusRevision: 489,
      statusReportId: 'new-session-bbb:9',
      lastRealRobloxStatusAt: '2026-06-19T14:29:28.395Z',
    };
    const merged = { ...existing, ...disk };
    sharded.preserveMonotonicLanes(existing, merged);
    assert.equal(merged.statusSessionId, 'new-session-bbb');
    assert.equal(merged.statusSeq, 9);
    assert.equal(merged.lastRealRobloxStatusAt, '2026-06-19T14:29:28.395Z');
  });

  test('same session keeps higher in-memory seq when disk seq regresses', () => {
    const existing = {
      statusSessionId: 'same-session',
      statusSeq: 12,
      statusRevision: 20,
      lastRealRobloxStatusAt: '2026-06-19T14:30:00.000Z',
    };
    const disk = {
      statusSessionId: 'same-session',
      statusSeq: 11,
      statusRevision: 19,
      lastRealRobloxStatusAt: '2026-06-19T14:29:00.000Z',
    };
    const merged = { ...existing, ...disk };
    sharded.preserveMonotonicLanes(existing, merged);
    assert.equal(merged.statusSeq, 12);
    assert.equal(merged.lastRealRobloxStatusAt, '2026-06-19T14:30:00.000Z');
  });
});
