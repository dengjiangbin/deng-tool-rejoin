'use strict';

// STRICT COMBINED P0 — data-completeness + timer + table/pagination proofs.
// Part A: owned fish/stone/totem instances must persist with NO 500 cap, so a
//         rare Ruby+Gemstone past index 500 is preserved + counted.
// Part B/C/D/E: source/compiled-asset assertions for timers, the reverted
//         scrollable desktop table, controls placement, and pagination.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const sessionStore = require('../src/fishitSessionStore');
const gameItemDb = require('../src/fishitGameItemDbPublic');
const ruby = require('../src/fishitRubyGemstoneCount');

function fishRow(name, mutation) {
  return {
    kind: 'fish',
    itemId: name === 'Ruby' ? '9001' : '1000',
    name,
    cleanName: name,
    baseFishName: name,
    baseName: name,
    quantity: 1,
    rarity: 'Legendary',
    mutation: mutation || null,
    mutationName: mutation || null,
  };
}

function stoneRow(i) {
  return { kind: 'stone', itemId: String(2000 + i), name: 'Enchant Stone', stoneType: 'Evolved', quantity: 1 };
}

function totemRow(i) {
  return { kind: 'totem', itemId: String(3000 + i), name: 'Totem', quantity: 1, category: 'totem' };
}

test('A2: fish upload with 501 instances persists all 501 (no 500 cap)', () => {
  const fish = [];
  for (let i = 0; i < 500; i += 1) fish.push(fishRow('Cerulean Dragon', null));
  fish.push(fishRow('Ruby', 'Gemstone')); // index 500 (the 501st)
  const out = sessionStore.sanitiseSession('captest501', {
    username: 'captest501', userId: 1, source: 'playerdata_gameitemdb',
    playerDataFishItems: fish,
  });
  assert.strictEqual(out.playerDataFishItems.length, 501, 'all 501 fish instances must persist');
});

test('A2: fish upload with 1000+ instances persists all instances', () => {
  const fish = [];
  for (let i = 0; i < 2000; i += 1) fish.push(fishRow('Cerulean Dragon', null));
  const out = sessionStore.sanitiseSession('captest2000', {
    username: 'captest2000', userId: 1, source: 'playerdata_gameitemdb',
    playerDataFishItems: fish,
  });
  assert.strictEqual(out.playerDataFishItems.length, 2000);
});

test('A2/F: Ruby+Gemstone at index 501 is preserved and counted', () => {
  const fish = [];
  for (let i = 0; i < 500; i += 1) fish.push(fishRow('Cerulean Dragon', null));
  fish.push(fishRow('Ruby', 'Gemstone'));
  const out = sessionStore.sanitiseSession('rubycap', {
    username: 'rubycap', userId: 1, source: 'playerdata_gameitemdb',
    playerDataFishItems: fish,
  });
  const rubyRows = out.playerDataFishItems.filter((r) => r.cleanName === 'Ruby' && /gemstone/i.test(r.mutation || ''));
  assert.strictEqual(rubyRows.length, 1, 'the rare Ruby+Gemstone past index 500 must survive persistence');
  // Server-side count agrees (grouped fish card path).
  const grouped = gameItemDb.groupFishRows(out.playerDataFishItems);
  const cards = grouped.map((g) => ({
    name: g.name, cleanName: g.cleanName || g.baseName,
    ownedInstances: (g.instances || []).map((inst) => ({ mutation: inst.mutation, mutationName: inst.mutationName })),
  }));
  assert.strictEqual(ruby.computeRubyGemstoneTopCard({ fishItems: cards }).count, 1);
});

test('A2: Ruby+Gemstone at the LAST index of a large list is preserved and counted', () => {
  const fish = [];
  for (let i = 0; i < 1500; i += 1) fish.push(fishRow('Cerulean Dragon', null));
  fish.push(fishRow('Ruby', 'Gemstone')); // last index 1500
  const out = sessionStore.sanitiseSession('rubylast', {
    username: 'rubylast', userId: 1, source: 'playerdata_gameitemdb',
    playerDataFishItems: fish,
  });
  assert.strictEqual(out.playerDataFishItems.length, 1501);
  const rubyRows = out.playerDataFishItems.filter((r) => r.cleanName === 'Ruby');
  assert.strictEqual(rubyRows.length, 1);
});

test('A3: stone inventory with 501+ instances is preserved', () => {
  const stones = [];
  for (let i = 0; i < 600; i += 1) stones.push(stoneRow(i));
  const out = sessionStore.sanitiseSession('stonecap', {
    username: 'stonecap', userId: 1, source: 'playerdata_gameitemdb',
    playerDataStoneItems: stones,
  });
  // Stones group by identity; 600 distinct ids => 600 grouped rows preserved (no 500 cap).
  assert.ok(out.playerDataStoneItems.length >= 501, `expected >=501 stone rows, got ${out.playerDataStoneItems.length}`);
});

test('A3: totem inventory with 501+ instances is preserved', () => {
  const totems = [];
  for (let i = 0; i < 700; i += 1) totems.push(totemRow(i));
  const out = sessionStore.sanitiseSession('totemcap', {
    username: 'totemcap', userId: 1, source: 'playerdata_gameitemdb',
    playerDataTotemItems: totems,
  });
  assert.strictEqual(out.playerDataTotemItems.length, 700);
});

test('A: groupFishRows preserves more than 1000 instances of one type (no per-group cap)', () => {
  const fish = [];
  for (let i = 0; i < 1500; i += 1) fish.push(fishRow('Cerulean Dragon', null));
  const grouped = gameItemDb.groupFishRows(fish);
  const card = grouped.find((g) => (g.cleanName || g.baseName || g.name) === 'Cerulean Dragon');
  assert.ok(card, 'grouped card must exist');
  assert.ok(card.instances.length >= 1100, `expected >1000 instances retained, got ${card.instances.length}`);
});

test('A5: upload body limit is raised well above 512KB (large inventories accepted)', () => {
  const coalesce = require('../src/trackerUploadCoalesce');
  // The middleware uses TRACKER_UPLOAD_MAX_BODY_BYTES; default must be > 1MB now.
  // Validate via the source constant indirectly: a 1MB content-length is allowed.
  const calls = [];
  const req = { headers: { 'content-length': String(1.5 * 1024 * 1024) }, body: { username: 'big' } };
  const res = {
    status(code) { calls.push(code); return this; },
    json() { return this; },
    headersSent: false,
  };
  let nexted = false;
  coalesce.trackerUploadCoalesceMiddleware(req, res, () => { nexted = true; });
  assert.ok(!calls.includes(413), '1.5MB upload must not be rejected with 413 anymore');
  assert.ok(nexted || calls.length === 0 || calls.includes(202), 'request should pass through, not be size-rejected');
});

// ---- UI / source assertions (Parts B, C, D, E) -----------------------------

const SRC = fs.readFileSync(path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'), 'utf8');

function compiledJs() {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'src', 'inventoryAssetManifest.json'), 'utf8'));
  const jsName = manifest.js || manifest.jsFile || Object.values(manifest).find((v) => typeof v === 'string' && v.endsWith('.js'));
  return fs.readFileSync(path.join(__dirname, '..', 'public', 'assets', jsName), 'utf8');
}
function compiledCss() {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'src', 'inventoryAssetManifest.json'), 'utf8'));
  const cssName = manifest.css || manifest.cssFile || Object.values(manifest).find((v) => typeof v === 'string' && v.endsWith('.css'));
  return fs.readFileSync(path.join(__dirname, '..', 'public', 'assets', cssName), 'utf8');
}

test('D: username controls render ABOVE the top summary card grid', () => {
  const controlsIdx = SRC.indexOf('class="player-control-bar"');
  const gridIdx = SRC.indexOf('id="inventoryStats"');
  assert.ok(controlsIdx > -1 && gridIdx > -1, 'both sections must exist');
  assert.ok(controlsIdx < gridIdx, 'player-control-bar must appear before the top summary grid');
});

test('C: mobile keeps the desktop table in a smooth horizontal scroll (no forced cards)', () => {
  const css = compiledCss();
  // Mobile must SHOW the table wrap and HIDE the mobile card list (reverted).
  assert.match(css, /accounts-table-wrap\{display:block ?!important/, 'table wrap must be shown on mobile');
  assert.match(css, /accounts-mobile-list\{display:none ?!important/, 'mobile cards must be hidden (reverted)');
  assert.match(css, /-webkit-overflow-scrolling:touch/, 'smooth touch scroll required');
  assert.match(css, /min-width:760px/, 'table needs a readable min-width for horizontal scroll');
});

test('C: Actions + Backpack columns and individual remove action remain in the table', () => {
  assert.match(SRC, /col-actions">Actions/);
  assert.match(SRC, /col-backpack">Backpack/);
  assert.match(SRC, /data-remove-account=/, 'individual row remove action must remain');
  assert.match(SRC, /data-open-backpack=/, 'backpack button must remain');
});

test('E1/E2: default page size 20 and modal options 20/50/100/1000', () => {
  assert.match(SRC, /ACCOUNTS_DEFAULT_PAGE_SIZE\s*=\s*20/);
  assert.match(SRC, /PAGE_SIZE_OPTIONS\s*=\s*\[20,\s*50,\s*100,\s*1000\]/);
  for (const n of [20, 50, 100, 1000]) {
    assert.match(SRC, new RegExp(`data-page-size="${n}"`), `page-size option ${n} must exist`);
  }
});

test('E3: First/Previous/Next/Last pagination controls exist', () => {
  for (const id of ['accountsPageFirst', 'accountsPagePrev', 'accountsPageNext', 'accountsPageLast']) {
    assert.match(SRC, new RegExp(`id="${id}"`), `${id} button must exist`);
  }
});

test('E/E4: pagination is applied after filter+sort and renders only the current page', () => {
  const js = compiledJs();
  // renderAccountsTable slices the filtered+sorted rows to the page window.
  assert.match(js, /getFilteredAccountEntries\(\)/);
  assert.match(js, /\.slice\(startIndex,\s*startIndex\s*\+\s*accountsPageSize\)/);
});

test('B: timer base is seeded from backend age (not Date.now on open)', () => {
  assert.match(SRC, /seedSectionBaseFromBackendAge/);
  assert.match(SRC, /firstObservation\s*=\s*entry\._trackerDisplaySig === undefined/);
  assert.match(SRC, /seedTimersFromBackend/);
});
