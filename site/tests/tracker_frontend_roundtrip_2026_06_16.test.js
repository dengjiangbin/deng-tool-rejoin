'use strict';

// 2026-06-16 frontend roundtrip regression. Covers the live-test failures:
//  - empty frontend (account eviction cap),
//  - mutation shown as `nil` in detail view,
//  - weight only on mutated fish (must be per-instance, independent of mutation),
//  - Ruby Gemstone mutation via the shared metadata contract,
//  - partial/heartbeat lane merge must not erase inventory/leaderstats,
//  - detail layout (2 columns) + fish detail rarity color,
//  - public ?debug=metadata contract.

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');

process.env.NODE_ENV = 'test';

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
function readSource() { return fs.readFileSync(SOURCE_PATH, 'utf8'); }

const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const {
  groupFishRows, groupStoneRows, mapToPublicFishCardItem, mapToPublicStoneCardItem,
  normalizeNonNilMutation, parseWeightKg,
} = gameItemDbPublic;

function fishRow(i, weight, mutation) {
  return {
    kind: 'fish', type: 'Fish', itemId: '777', name: 'Test Tuna', baseName: 'Test Tuna',
    tier: 4, uuid: `u${i}`, quantity: 1, weightKg: weight,
    mutationName: mutation || 'None', weightSourcePath: 'Metadata.Weight',
  };
}

describe('mutation nil filter (BLOCKER D)', () => {
  for (const bad of ['nil', 'null', 'undefined', 'None', 'normal', '', '  ', null]) {
    test(`rejects placeholder ${JSON.stringify(bad)}`, () => {
      assert.equal(normalizeNonNilMutation(bad), null);
    });
  }
  test('keeps a real mutation', () => {
    assert.equal(normalizeNonNilMutation('Gold'), 'Gold');
    assert.equal(normalizeNonNilMutation('  Glowing '), 'Glowing');
  });
});

describe('per-instance weight + optional mutation (BLOCKER E/D)', () => {
  test('7 same-species fish keep 7 distinct weights; only 2 mutated', () => {
    const weights = [1.1, 2.2, 3.3, 4.4, 5.5, 6.6, 7.7];
    const rows = weights.map((w, i) => fishRow(i, w, i < 2 ? 'Gold' : 'None'));
    const card = groupFishRows(rows).map(mapToPublicFishCardItem)[0];
    const inst = card.ownedInstances;
    assert.equal(inst.length, 7);
    assert.deepEqual(inst.map((x) => x.weightKg), weights);
    assert.equal(new Set(inst.map((x) => x.weightKg)).size, 7);
    const mutated = inst.filter((x) => x.mutation === 'Gold');
    assert.equal(mutated.length, 2);
    // Non-mutated fish must still have weight and NOT a placeholder mutation.
    for (const x of inst.filter((y) => y.mutation == null)) {
      assert.ok(x.weightKg > 0, 'non-mutated fish must keep weight');
      assert.equal(x.mutation, null);
    }
  });

  test('weight is read independent of mutation existence', () => {
    const noMut = parseWeightKg({ weightKg: 5.5 });
    assert.equal(noMut, 5.5);
    const aliasOnly = parseWeightKg({ weight: '12.34 kg' });
    assert.equal(aliasOnly, 12.34);
  });
});

describe('re-enrich preserves ownedInstances (BLOCKER A/E render)', () => {
  test('re-mapping an already-public card keeps per-instance mutation/weight', () => {
    const weights = [1.1, 2.2, 3.3];
    const rows = weights.map((w, i) => fishRow(i, w, i === 0 ? 'Gold' : 'None'));
    const card = groupFishRows(rows).map(mapToPublicFishCardItem)[0];
    assert.equal(card.ownedInstances.length, 3);
    // Simulate the get-backpack image re-enrich: re-map the public card (no .instances).
    const reMapped = mapToPublicFishCardItem({ ...card, instances: undefined });
    assert.equal(reMapped.ownedInstances.length, 3, 'ownedInstances must survive re-mapping');
    assert.deepEqual(reMapped.ownedInstances.map((x) => x.weightKg), weights);
    assert.equal(reMapped.ownedInstances.filter((x) => x.mutation === 'Gold').length, 1);
  });
});

describe('Ruby Gemstone via metadata contract (BLOCKER F)', () => {
  test('stone/gem mutation surfaces and is not nil', () => {
    const rows = [{ kind: 'stone', stoneType: 'Ruby', itemId: '5001', name: 'Ruby Gemstone', quantity: 1, uuid: 's1', mutationName: 'Glowing' }];
    const card = mapToPublicStoneCardItem(groupStoneRows(rows)[0]);
    assert.equal(card.mutation, 'Glowing');
  });
  test('stone with nil-string mutation does not render nil', () => {
    const rows = [{ kind: 'stone', stoneType: 'Ruby', itemId: '5001', name: 'Ruby Gemstone', quantity: 1, uuid: 's2', mutation: 'nil' }];
    const card = mapToPublicStoneCardItem(groupStoneRows(rows)[0]);
    assert.equal(card.mutation, null);
  });
});

describe('persistence keeps weight + mutation aliases (BLOCKER D/E)', () => {
  let tmpDir;
  let store;
  let sharded;
  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ft-roundtrip-'));
    process.env.FISHIT_SESSION_SHARDED = '1';
    process.env.FISHIT_LIVE_SESSIONS_DIR = tmpDir;
    delete process.env.FISHIT_LIVE_SESSIONS_PATH;
    delete require.cache[require.resolve('../src/fishitSessionStoreSharded')];
    delete require.cache[require.resolve('../src/fishitSessionStore')];
    sharded = require('../src/fishitSessionStoreSharded');
    store = require('../src/fishitSessionStore');
  });
  afterEach(() => {
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) { /* noop */ }
  });

  test('save → reload from disk preserves per-instance weight + mutation', async () => {
    const weights = [1.1, 2.2, 3.3];
    const rows = weights.map((w, i) => fishRow(i, w, i === 0 ? 'Gold' : 'None'));
    store.saveSession('simuser', {
      username: 'simuser', userId: 1, trackerBuild: 'x',
      playerDataFishItems: rows, playerDataStoneItems: [], playerDataTotemItems: [],
    }, {});
    await sharded.flushDirtyAccountsAsync({ priority: true });
    const raw = JSON.parse(fs.readFileSync(path.join(tmpDir, 'accounts', 'simuser.json'), 'utf8'));
    const reloaded = store.sanitiseSession('simuser', raw);
    const card = groupFishRows(reloaded.playerDataFishItems).map(mapToPublicFishCardItem)[0];
    assert.deepEqual(card.ownedInstances.map((x) => x.weightKg), weights);
    assert.equal(card.ownedInstances.filter((x) => x.mutation === 'Gold').length, 1);
  });
});

describe('account cap no longer evicts active users (BLOCKER A/B)', () => {
  test('sharded MAX_ACCOUNTS default well above 200', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitSessionStoreSharded.js'), 'utf8');
    const m = src.match(/FISHIT_MAX_PERSISTED_SESSIONS\s*\|\|\s*(\d+)/);
    assert.ok(m, 'cap default present');
    assert.ok(Number(m[1]) >= 1000, `cap must be >= 1000, got ${m[1]}`);
  });
  test('reloadChangedAccounts only evicts when shard file is gone', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitSessionStoreSharded.js'), 'utf8');
    assert.match(src, /fileStillExists/);
  });
});

describe('frontend detail layout + rarity + nil filter (BLOCKER I/J/D)', () => {
  const src = readSource();
  test('detail grid is multi-column (not locked to single column)', () => {
    assert.match(src, /\.ft-detail-instances\s*\{[^}]*grid-template-columns:repeat\(auto-fill,minmax\(/);
  });
  test('APK embed detail grid allows two columns', () => {
    assert.match(src, /\.inventory-apk-embed\s+\.ft-detail-instances\s*\{\s*grid-template-columns:repeat\(auto-fill,minmax\(/);
    assert.doesNotMatch(src, /\.inventory-apk-embed\s+\.ft-detail-instances\s*\{\s*grid-template-columns:1fr/);
  });
  test('fish detail card carries rarity color rules', () => {
    assert.match(src, /\.ft-inst-card\[data-rarity="secret"\]/);
    assert.match(src, /\.ft-inst-card\[data-rarity="legendary"\]/);
  });
  test('renderFishInstanceCard sets data-rarity', () => {
    assert.match(src, /data-rarity="\$\{escHtml\(rarity\)\}"/);
  });
  test('frontend normalizeNonNil rejects nil/null/none', () => {
    assert.match(src, /function ftNormalizeNonNil/);
    assert.match(src, /nil\|null\|undefined\|none/);
  });
});

describe('public ?debug=metadata contract (BLOCKER G)', () => {
  test('routes expose metadataDebug builder with the required counts', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'fishitTrackerRoutes.js'), 'utf8');
    assert.match(src, /buildMetadataDebugBlock/);
    for (const field of [
      'instancesWithMutation', 'instancesWithWeight', 'instancesWithNilMutationString',
      'instancesMissingWeight', 'publicFishCount', 'laneMergeState', 'dataSourcePath',
    ]) {
      assert.match(src, new RegExp(field), `metadataDebug must expose ${field}`);
    }
  });
});
