'use strict';

/**
 * Regression (2026-06-18) — reportIdentitySource / leaderstatsIdentitySource /
 * inventoryIdentitySource.
 *
 * The API must report client_explicit ONLY when the NEW Luau reporter contract
 * is present (statusReportId, or sessionId+statusSeq; leaderstatsReportId or
 * leaderstatsSeq; inventoryReportId or inventorySeq/inventoryHash). Legacy fields
 * (runId / uploadSeq / executionSessionId) must still be classified as
 * backend_derived so an old client cannot masquerade as an explicit-identity one.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const identity = require('../src/trackerReportIdentity');

const NOW = Date.parse('2026-06-18T12:00:00.000Z');

test('status: explicit statusReportId -> client_explicit', () => {
  const r = identity.applyReport('status', {}, {
    username: 'A', sessionId: 'sess-1', statusSeq: 1,
    statusReportId: 'sess-1:1', capturedAt: NOW, online: true,
  }, NOW);
  assert.equal(r.identitySource, 'client_explicit');
  assert.equal(r.updates.reportIdentitySource, 'client_explicit');
});

test('status: sessionId + statusSeq (no reportId) -> client_explicit', () => {
  const r = identity.applyReport('status', {}, {
    username: 'A', sessionId: 'sess-1', statusSeq: 3, online: true,
  }, NOW);
  assert.equal(r.identitySource, 'client_explicit');
});

test('status: legacy runId/uploadSeq only -> backend_derived', () => {
  const r = identity.applyReport('status', {}, {
    username: 'A', runId: 'run-9', uploadSeq: 7, online: true,
  }, NOW);
  assert.equal(r.identitySource, 'backend_derived');
  assert.equal(r.updates.reportIdentitySource, 'backend_derived');
});

test('leaderstats: explicit leaderstatsReportId -> client_explicit', () => {
  const r = identity.applyReport('leaderstats', {}, {
    username: 'A', leaderstatsReportId: 'sess-1:5', leaderstatsSeq: 5,
  }, NOW);
  assert.equal(r.identitySource, 'client_explicit');
  assert.equal(r.updates.leaderstatsIdentitySource, 'client_explicit');
});

test('leaderstats: legacy leaderstatsUploadSeq -> backend_derived', () => {
  const r = identity.applyReport('leaderstats', {}, {
    username: 'A', leaderstatsUploadSeq: 4,
  }, NOW);
  assert.equal(r.identitySource, 'backend_derived');
});

test('inventory: explicit inventoryReportId/hash -> client_explicit', () => {
  const r = identity.applyReport('inventory', {}, {
    username: 'A', inventoryReportId: 'sess-1:2', inventorySeq: 2, inventoryHash: 'abc123',
  }, NOW);
  assert.equal(r.identitySource, 'client_explicit');
  assert.equal(r.updates.inventoryIdentitySource, 'client_explicit');
  assert.equal(r.updates.inventoryHash, 'abc123');
});

test('inventory: legacy uploadSeq only -> backend_derived', () => {
  const r = identity.applyReport('inventory', {}, {
    username: 'A', uploadSeq: 9,
  }, NOW);
  assert.equal(r.identitySource, 'backend_derived');
});

test('identitySource is recorded even on a STALE/duplicate report', () => {
  // First explicit report establishes identity.
  const first = identity.applyReport('status', {}, {
    username: 'A', sessionId: 'sess-1', statusSeq: 1, statusReportId: 'sess-1:1', online: true,
  }, NOW);
  const session = { ...first.updates };
  // Duplicate same reportId -> stale, but still explicit contract.
  const dup = identity.applyReport('status', session, {
    username: 'A', sessionId: 'sess-1', statusSeq: 1, statusReportId: 'sess-1:1', online: true,
  }, NOW + 1000);
  assert.equal(dup.fresh, false);
  assert.equal(dup.reason, 'duplicate_report_id');
  assert.equal(dup.updates.reportIdentitySource, 'client_explicit');
});
