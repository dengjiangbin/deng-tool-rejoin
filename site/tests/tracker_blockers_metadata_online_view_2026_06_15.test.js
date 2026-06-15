'use strict';

// BLOCKER regression suite (2026-06-15):
//  B  per-instance mutation+weight survive Lua->backend->detail (7 King Crab fixture)
//  C  ONLINE/ACCOUNTS count uses the SAME shared predicate as the green row status
//  E  switching view/tab clears the inline detail state (no stuck detail)
// Also pins the BLOCKER A nil-safe metadata helpers in the private Lua source.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const { groupFishRows, mapToPublicFishCardItem } = gameItemDbPublic;

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const RAW_TRACKER_LUA = path.join(__dirname, '..', '..', '..', 'DENG PRIVATE SOURCE', 'fishtracker', 'tracker.lua');

function readSource() { return fs.readFileSync(SOURCE_PATH, 'utf8'); }

function sliceBalanced(src, startToken, open, close) {
  const start = src.indexOf(startToken);
  if (start === -1) throw new Error(`token not found: ${startToken}`);
  const from = src.indexOf(open, start);
  let depth = 0;
  for (let i = from; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === open) depth += 1;
    else if (ch === close) { depth -= 1; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error(`unbalanced block: ${startToken}`);
}

// 7 King Crab: 2 GOLD (different weights), 1 SHINY (weight), 3 normal (weight),
// 1 missing-weight fallback.
function sevenKingCrab() {
  return [
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-1', mutation: 'Gold', weightKg: 9.3, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-2', mutation: 'Gold', weightKg: 7.8, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-3', mutation: 'Shiny', weightKg: 6.0, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-4', mutation: 'None', weightKg: 5.2, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-5', mutation: 'None', weightKg: 4.1, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-6', mutation: 'None', weightKg: 3.7, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-7', mutation: 'None', icon: 'rbxassetid://1' },
  ];
}

describe('BLOCKER B — 7 King Crab fixture preserves per-instance mutation + weight', () => {
  test('overview aggregates to one group of 7; detail exposes 7 instances', () => {
    const grouped = groupFishRows(sevenKingCrab());
    assert.equal(grouped.length, 1, 'one aggregate card');
    assert.equal(grouped[0].quantity, 7, 'overview keeps quantity 7');
    const card = mapToPublicFishCardItem(grouped[0]);
    assert.equal(card.ownedInstances.length, 7, 'detail has one card per owned fish');
    const gold = card.ownedInstances.filter((i) => i.mutation === 'Gold');
    const shiny = card.ownedInstances.filter((i) => i.mutation === 'Shiny');
    assert.equal(gold.length, 2, 'two GOLD instances');
    assert.equal(shiny.length, 1, 'one SHINY instance');
    assert.notEqual(gold[0].weightKg, gold[1].weightKg, 'two GOLD have different weights');
    const missing = card.ownedInstances.filter((i) => i.weightKg == null);
    assert.equal(missing.length, 1, 'exactly one instance has no weight (fallback)');
    const withWeight = card.ownedInstances.filter((i) => typeof i.weightKg === 'number' && i.weightKg > 0);
    assert.equal(withWeight.length, 6, 'six instances carry real weight');
    assert.equal(card.mutation, null, 'aggregate card mutation stays hidden');
  });

  test('backend reads weight + mutation from nested Metadata shapes', () => {
    const rows = [
      { kind: 'fish', type: 'Fish', itemId: '901', name: 'Whale', baseName: 'Whale', quantity: 1, uuid: 'w-1', Metadata: { Mutation: 'Frozen', Weight: '1980.1' } },
    ];
    const card = mapToPublicFishCardItem(groupFishRows(rows)[0]);
    assert.equal(card.ownedInstances.length, 1);
    assert.equal(card.ownedInstances[0].mutation, 'Frozen', 'mutation read from Metadata.Mutation');
    assert.ok(card.ownedInstances[0].weightKg > 0, 'weight read from Metadata.Weight');
  });
});

describe('BLOCKER B — detail search by name + mutation (shipped ftFilterInstanceCards)', () => {
  const src = readSource();
  const filterSrc = sliceBalanced(src, 'function ftFilterInstanceCards(', '{', '}');
  // eslint-disable-next-line no-new-func
  const ftFilterInstanceCards = new Function(`${filterSrc}; return ftFilterInstanceCards;`)();
  const cards = [
    { name: 'King Crab', mutation: 'Gold' }, { name: 'King Crab', mutation: 'Gold' },
    { name: 'King Crab', mutation: 'Shiny' }, { name: 'King Crab', mutation: '' },
    { name: 'King Crab', mutation: '' }, { name: 'King Crab', mutation: '' },
    { name: 'King Crab', mutation: '' },
  ];
  test('search "Gold" returns exactly 2', () => {
    assert.equal(ftFilterInstanceCards(cards, 'Gold').length, 2);
  });
  test('search "King" returns all 7; empty restores all', () => {
    assert.equal(ftFilterInstanceCards(cards, 'King').length, 7);
    assert.equal(ftFilterInstanceCards(cards, '').length, 7);
  });
  test('combined "Gold King" matches the 2 Gold King Crab', () => {
    assert.equal(ftFilterInstanceCards(cards, 'Gold King').length, 2);
  });
  test('case-insensitive', () => {
    assert.equal(ftFilterInstanceCards(cards, 'gOlD').length, 2);
  });
});

describe('BLOCKER C — shared online predicate (row status == summary count)', () => {
  const src = readSource();
  const sandbox = [
    'var ACCOUNT_PRESENCE_GRACE_MS = 600 * 1000;',
    'function entryUploadStatus(e){ return e && e.uploadStatus ? e.uploadStatus : null; }',
    sliceBalanced(src, 'function presenceContactMsFrom(', '{', '}'),
    sliceBalanced(src, 'function trackerPresenceContactMs(', '{', '}'),
    sliceBalanced(src, 'function isTrackerAccountOnline(', '{', '}'),
  ].join('\n');
  // eslint-disable-next-line no-new-func
  const isTrackerAccountOnline = new Function(`${sandbox}; return isTrackerAccountOnline;`)();
  const now = 1_000_000_000_000;
  const at = (ms) => new Date(ms).toISOString();

  test('row fresh 90s ago counts as online (1/1, not 0/1)', () => {
    const entry = { uploadStatus: { lastSuccessfulHeartbeatAt: at(now - 90 * 1000) } };
    assert.equal(isTrackerAccountOnline(entry, now), true);
  });
  test('explicit live presence flag is online', () => {
    const entry = { uploadStatus: { accountPresenceLive: true } };
    assert.equal(isTrackerAccountOnline(entry, now), true);
  });
  test('failed/intermittent refresh within grace stays online', () => {
    // No fresh contact this poll, but last good contact was 2 min ago.
    const entry = { _presenceContactMs: now - 120 * 1000, uploadStatus: {} };
    assert.equal(isTrackerAccountOnline(entry, now), true);
  });
  test('server-confirmed client_offline drops it even within grace', () => {
    const entry = { uploadStatus: { lastSuccessfulHeartbeatAt: at(now - 30 * 1000), accountPresenceReason: 'client_offline' } };
    assert.equal(isTrackerAccountOnline(entry, now), false);
  });
  test('beyond the 10-minute grace it becomes offline', () => {
    const entry = { uploadStatus: { lastSuccessfulHeartbeatAt: at(now - 11 * 60 * 1000) } };
    assert.equal(isTrackerAccountOnline(entry, now), false);
  });

  test('row status + summary count both delegate to isTrackerAccountOnline', () => {
    assert.match(src, /function entryConnectionFreshness[\s\S]*?isTrackerAccountOnline\(entry, Date\.now\(\)\)/);
    assert.match(src, /function isTrackerOnline[\s\S]*?isTrackerAccountOnline\(entry, Date\.now\(\)\)/);
    assert.match(src, /totalAccounts: localStats\.totalAccounts/);
    assert.match(src, /onlineCount: localStats\.onlineCount/);
  });
});

describe('BLOCKER E — switching view/tab clears inline detail state', () => {
  const src = readSource();
  test('setAccountViewMode calls clearInlineDetailState', () => {
    const fn = sliceBalanced(src, 'function setAccountViewMode(', '{', '}');
    assert.match(fn, /clearInlineDetailState\(/);
  });
  test('clearInlineDetailState closes the detail panel', () => {
    const fn = sliceBalanced(src, 'function clearInlineDetailState(', '{', '}');
    assert.match(fn, /closeFtDetail\(\)/);
  });
});

describe('BLOCKER A — private Lua metadata scan is nil-safe', () => {
  const exists = fs.existsSync(RAW_TRACKER_LUA);
  const testIf = exists ? test : test.skip;
  testIf('readItemWeightKg uses normalizeWeightKg, never the bare parseWeight local', () => {
    const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
    assert.match(lua, /function LiveSafe\.safeGetNested/);
    assert.match(lua, /function LiveSafe\.normalizeWeightKg/);
    assert.match(lua, /function LiveSafe\.normalizeMutationName/);
    assert.match(lua, /function LiveSafe\.readItemMutation/);
    const start = lua.indexOf('function LiveSafe.readItemWeightKg');
    const body = lua.slice(start, lua.indexOf('\nend', start));
    assert.ok(body.includes('LiveSafe.normalizeWeightKg'), 'uses normalizeWeightKg');
    assert.ok(!/[^.]parseWeight\(/.test(body), 'no bare parseWeight() call (the nil-call cause)');
  });
  testIf('inventory scan reads Metadata.Mutation and guards each row with pcall', () => {
    const lua = fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
    assert.match(lua, /LiveSafe\.readItemMutation\(item\)/);
    assert.match(lua, /INVENTORY_ROW_SKIPPED/);
    assert.match(lua, /"Metadata", "Mutation"/);
  });
});
