'use strict';

/**
 * Client contract regression (2026-06-18) — the deployed Luau reporter must emit
 * explicit per-lane source-of-truth identity and use a 50-55s cadence (not 60s).
 *
 * The reporter dist (dist/tracker.lua, base64-wrapped) is built from the private
 * raw source and is gitignored, so this test SKIPS when the built dist is not
 * present (fresh clone / CI without the private source). When it IS present
 * (local build + deploy), it decodes the wrapper and locks the client contract
 * so a future build can never silently drop the identity fields or revert the
 * cadence.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DIST = path.join(__dirname, '..', '..', 'dist', 'tracker.lua');

function decodeDist(distSrc) {
  // Be CRLF-tolerant: dist files built on Windows ship with \r\n line endings,
  // POSIX checkouts ship with \n. The base64 body is the only thing we need.
  const m = distSrc.match(/local __B=\[\[([\s\S]*?)\]\]\r?\nlocal __A=/);
  if (!m) throw new Error('dist decode anchor missing');
  return Buffer.from(m[1], 'base64').toString('utf8');
}

const present = fs.existsSync(DIST);

test('deployed reporter dist emits explicit 3-lane identity + 50-55s cadence', { skip: !present ? 'dist/tracker.lua not built (private source absent)' : false }, () => {
  const decoded = decodeDist(fs.readFileSync(DIST, 'utf8'));

  // Status lane explicit identity.
  for (const field of ['sessionId', 'statusSeq', 'statusReportId', 'capturedAt', 'sentAt', 'placeId', 'jobId']) {
    assert.ok(decoded.includes(field), `status lane must emit ${field}`);
  }
  // Leaderstats lane explicit identity.
  for (const field of ['leaderstatsSeq', 'leaderstatsReportId', 'leaderstatsCapturedAt', 'leaderstatsSentAt']) {
    assert.ok(decoded.includes(field), `leaderstats lane must emit ${field}`);
  }
  // Inventory lane explicit identity + hash.
  for (const field of ['inventorySeq', 'inventoryReportId', 'inventoryCapturedAt', 'inventorySentAt', 'inventoryHash']) {
    assert.ok(decoded.includes(field), `inventory lane must emit ${field}`);
  }

  // Monotonic per-session identity helpers exist (sessionId generated once).
  assert.ok(decoded.includes('TrackerIdentity.ensureSession'), 'sessionId must be generated once per session');
  assert.ok(decoded.includes('TrackerIdentity.nextStatus'), 'status seq helper');
  assert.ok(decoded.includes('TrackerIdentity.nextLeaderstats'), 'leaderstats seq helper');
  assert.ok(decoded.includes('TrackerIdentity.nextInventory'), 'inventory seq helper');

  // Cadence target is 50-55s, NOT 60s.
  const m = decoded.match(/lightSyncIntervalSeconds\s*=\s*(\d+)/);
  assert.ok(m, 'lightSyncIntervalSeconds must be set');
  const cadence = Number(m[1]);
  assert.ok(cadence >= 50 && cadence <= 55, `cadence ${cadence}s must be 50-55s`);

  // Jitter is applied so heartbeat never aligns exactly with the 60s boundary.
  assert.ok(/baseInterval \+ math\.random/.test(decoded), 'status heartbeat must jitter');
});
