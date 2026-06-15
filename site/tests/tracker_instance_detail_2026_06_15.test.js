'use strict';

// Inline detail / instance cards (2026-06-15): proves the click-through detail
// view exposes one row per owned fish instance with its OWN mutation + weight +
// image, and that the detail search filters by name and mutation. Also pins the
// public dist Lua to the build that emits per-instance mutation/weight.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

process.env.NODE_ENV = 'test';

const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const { groupFishRows, mapToPublicFishCardItem, buildPublicFromPlayerDataGameItemDb } = gameItemDbPublic;

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const manifest = require('../src/inventoryAssetManifest.json');
const INVENTORY_JS = path.join(__dirname, '..', 'public', 'assets', manifest.js);
const INVENTORY_CSS = path.join(__dirname, '..', 'public', 'assets', manifest.css);
const DIST_LUA = path.join(__dirname, '..', '..', 'dist', 'tracker.lua');
const { PRODUCTION_TRACKER_BUILD } = require('../src/fishitTrackerBuild');

function readSource() { return fs.readFileSync(SOURCE_PATH, 'utf8'); }

function sliceBalanced(src, startToken, open, close) {
  const start = src.indexOf(startToken);
  if (start === -1) throw new Error(`token not found: ${startToken}`);
  const from = src.indexOf(open, start);
  let depth = 0;
  for (let i = from; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === open) depth += 1;
    else if (ch === close) {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced block: ${startToken}`);
}

// Build a fake upload of N King Crab instances with mixed mutation + weight.
function kingCrabRows() {
  return [
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-1', mutation: 'None', weightKg: 5.2, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-2', mutation: 'Gold', weightKg: 7.8, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-3', mutation: 'Shiny', weightKg: 6.0, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-4', mutation: 'None', weightKg: 4.1, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-5', mutation: 'Gold', weightKg: 9.3, icon: 'rbxassetid://1' },
    { kind: 'fish', type: 'Fish', itemId: '900', name: 'King Crab', baseName: 'King Crab', quantity: 1, uuid: 'kc-6', mutation: 'None', weightKg: 3.7, icon: 'rbxassetid://1' },
  ];
}

describe('inline detail per-instance pipeline (backend)', () => {
  test('groupFishRows preserves one instance per owned fish with own mutation + weight', () => {
    const grouped = groupFishRows(kingCrabRows());
    assert.equal(grouped.length, 1, 'six King Crab aggregate into one group card');
    const group = grouped[0];
    assert.equal(group.quantity, 6, 'aggregate quantity is preserved');
    assert.ok(Array.isArray(group.instances), 'instances array exists');
    assert.equal(group.instances.length, 6, 'six per-instance rows preserved before aggregation');
    const gold = group.instances.filter((i) => i.mutation === 'Gold');
    assert.equal(gold.length, 2, 'Gold mutation preserved per instance');
    assert.ok(group.instances.every((i) => typeof i.weightKg === 'number' && i.weightKg > 0), 'each instance keeps its own weight');
    // "None" must NOT become a placeholder mutation.
    assert.ok(group.instances.some((i) => i.mutation === null), 'unmutated fish carry null mutation, not "None"');
  });

  test('mapToPublicFishCardItem exposes ownedInstances but still hides mutation/weight on the aggregate card', () => {
    const grouped = groupFishRows(kingCrabRows());
    const card = mapToPublicFishCardItem(grouped[0]);
    // Aggregate (overview) card behaviour unchanged.
    assert.equal(card.mutation, null, 'overview card mutation stays hidden');
    assert.equal(card.publicWeightHidden, true, 'overview card weight stays hidden');
    // Per-instance detail data is present.
    assert.ok(Array.isArray(card.ownedInstances), 'ownedInstances present');
    assert.equal(card.ownedInstances.length, 6, 'six instance rows for six owned fish');
    assert.equal(card.ownedInstanceCount, 6);
    const inst = card.ownedInstances.find((i) => i.mutation === 'Gold');
    assert.ok(inst, 'Gold instance exposed');
    assert.ok(inst.weightKg > 0, 'Gold instance carries real weight');
    assert.equal(inst.baseFishName, 'King Crab');
  });

  test('end-to-end public view returns ownedInstances with mutation + weight + resolvable image', async () => {
    const sessionData = {
      inventorySource: gameItemDbPublic.PLAYERDATA_GAMEITEMDB_SOURCE,
      sourceTruth: gameItemDbPublic.defaultSourceTruth(),
      playerDataFishItems: kingCrabRows(),
    };
    const view = await buildPublicFromPlayerDataGameItemDb(sessionData, 'https://aio.deng.my.id', {});
    assert.ok(Array.isArray(view.fishItems) && view.fishItems.length === 1, 'one fish group');
    const fish = view.fishItems[0];
    assert.ok(Array.isArray(fish.ownedInstances) && fish.ownedInstances.length === 6, 'six instances served by API');
    assert.ok(fish.ownedInstances.some((i) => i.mutation === 'Gold' && i.weightKg > 0), 'API instance has mutation + weight');
    // Image resolver fields available on the group (same source overview cards use).
    assert.ok('imageUrl' in fish && 'imageSource' in fish && 'itemId' in fish, 'image resolver fields present');
  });
});

// Extract the REAL shipped search filter from the source EJS and exercise it.
function loadFilter() {
  const src = readSource();
  const code = sliceBalanced(src, 'function ftFilterInstanceCards(', '{', '}');
  // eslint-disable-next-line no-new-func
  return new Function(`${code}; return ftFilterInstanceCards;`)();
}

describe('inline detail search (shipped logic)', () => {
  const cards = [
    { name: 'King Crab', mutation: '' },
    { name: 'King Crab', mutation: 'Gold' },
    { name: 'King Crab', mutation: 'Shiny' },
    { name: 'Fangtooth', mutation: 'Gold' },
    { name: 'Stingray', mutation: '' },
  ];
  const filter = loadFilter();

  test('empty query restores all instances', () => {
    assert.equal(filter(cards, '').length, cards.length);
    assert.equal(filter(cards, '   ').length, cards.length);
  });
  test('search by fish name', () => {
    const r = filter(cards, 'king crab');
    assert.equal(r.length, 3);
    assert.ok(r.every((c) => c.name === 'King Crab'));
  });
  test('search by mutation', () => {
    const r = filter(cards, 'gold');
    assert.equal(r.length, 2, 'both Gold instances (King Crab + Fangtooth)');
  });
  test('combined mutation + name ("Gold King") matches Gold King Crab', () => {
    const r = filter(cards, 'Gold King');
    assert.equal(r.length, 1);
    assert.equal(r[0].name, 'King Crab');
    assert.equal(r[0].mutation, 'Gold');
  });
  test('no match yields empty (drives the empty-state message)', () => {
    assert.equal(filter(cards, 'zzz nothing').length, 0);
  });
});

describe('inline detail render assets', () => {
  const js = fs.readFileSync(INVENTORY_JS, 'utf8');
  const css = fs.readFileSync(INVENTORY_CSS, 'utf8');
  test('built JS renders image + mutation + weight per instance and wires search', () => {
    assert.ok(js.includes('ft-inst-card__img'), 'instance card image element shipped');
    assert.ok(js.includes('ownedInstances'), 'instance data consumed in built JS');
    assert.ok(js.includes('ftFilterInstanceCards'), 'search filter shipped');
    assert.ok(js.includes('ftRenderFishInstances'), 'search re-render shipped');
    assert.ok(js.includes('No matching fish instances'), 'empty-state message shipped');
    assert.ok(js.includes('Weight unknown'), 'weight fallback shipped');
  });
  test('built CSS styles the instance image, body, and detail search', () => {
    assert.ok(css.includes('.ft-inst-card__img'), 'instance image style');
    assert.ok(css.includes('.ft-detail-search__input'), 'detail search input style');
  });
});

describe('public dist Lua marker + endpoints', () => {
  const dist = fs.readFileSync(DIST_LUA, 'utf8');
  function decoded() {
    const m = dist.match(/local __B=\[\[([\s\S]*?)\]\]\nlocal __A=/);
    assert.ok(m, 'dist base64 anchor present');
    return Buffer.from(m[1], 'base64').toString('utf8');
  }
  test('dist build marker matches the production marker (per-instance build)', () => {
    const d = decoded();
    const m = d.match(/TRACKER_BUILD\s*=\s*"([^"]+)"/);
    assert.ok(m, 'TRACKER_BUILD present');
    assert.equal(m[1], PRODUCTION_TRACKER_BUILD);
    assert.equal(PRODUCTION_TRACKER_BUILD, 'INSTANCE_MUTATION_WEIGHT_DETAIL_2026_06_15');
  });
  test('compact fish upload emits per-instance mutation + weight', () => {
    const d = decoded();
    assert.ok(d.includes('out.weightKg = w'), 'weightKg emitted in compact fish row');
    assert.ok(d.includes('out.mutation = row.mutation'), 'mutation emitted in compact row');
    assert.ok(d.includes('LiveSafe.readItemWeightKg'), 'weight extraction helper present');
  });
  test('permanent loader endpoints unchanged (no versioned URL, correct ingest paths)', () => {
    const d = decoded();
    assert.ok(d.includes('aio.deng.my.id/api/fishit-tracker/update-backpack'), 'backpack endpoint intact');
    assert.ok(d.includes('aio.deng.my.id/api/tracker/update-catalog'), 'catalog endpoint intact');
    assert.ok(!/update-backpack\?v=/.test(d), 'no versioned upload URL introduced');
  });
});
