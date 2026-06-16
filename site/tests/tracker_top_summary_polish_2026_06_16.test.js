'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

process.env.NODE_ENV = process.env.NODE_ENV || 'production';

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const COUNT_UP_PATH = path.join(__dirname, '..', 'public', 'js', 'count-up-stats.js');
const src = fs.readFileSync(SOURCE_PATH, 'utf8');
const countUpSrc = fs.readFileSync(COUNT_UP_PATH, 'utf8');

function topGridBlock() {
  const start = src.indexOf('tracker-top-summary-grid" id="inventoryStats"');
  return src.slice(start, start + 5200);
}

describe('tracker top summary card layout (3 columns, all platforms)', () => {
  test('inventoryStats uses dedicated tracker-top-summary grid/card classes', () => {
    assert.match(src, /tracker-top-summary-grid" id="inventoryStats"/);
    assert.match(src, /class="tracker-top-summary-card tracker-top-summary-card--online"/);
    assert.match(src, /class="tracker-top-summary-card tracker-top-summary-card--ruby"/);
    assert.match(src, /class="tracker-top-summary-card tracker-top-summary-card--runic"/);
  });

  test('grid is exactly three columns on desktop', () => {
    assert.match(src, /#inventoryStats\.tracker-top-summary-grid\s*\{[^}]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
  });

  test('mobile (<=640px) STAYS three columns (only shrinks)', () => {
    assert.match(src, /@media \(max-width:640px\)[\s\S]*#inventoryStats\.tracker-top-summary-grid[\s\S]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
    assert.doesNotMatch(src, /@media \(max-width:640px\)[\s\S]*#inventoryStats\.tracker-top-summary-grid[^}]*grid-template-columns:1fr/);
  });
});

describe('tracker top summary card ORDER is exact (6 cards)', () => {
  test('order is Account, Secret, Forgotten, Ruby, Evolved, Runic', () => {
    const block = topGridBlock();
    const labels = [...block.matchAll(/tracker-top-summary-label">([^<]+)</g)].map((m) => m[1].trim());
    assert.deepEqual(labels.slice(0, 6), [
      'Online / Accounts',
      'Secret Fish',
      'Forgotten Fish',
      'Ruby Gemstone',
      'Evolved Enchant Stone',
      'Runic Stone',
    ]);
  });

  test('Ruby Gemstone card and stat binding restored', () => {
    assert.match(src, /id="statRubyGemstone"/);
    assert.match(src, /statRubyGemstoneEl/);
    assert.match(src, /stats\.rubyGemstone/);
  });
});

describe('tracker top summary typography + green online count', () => {
  test('online count green !important, slash/total white', () => {
    assert.match(src, /\.tracker-online-value \.online-count\s*\{[^}]*color:#62e68a !important/);
    assert.match(src, /\.tracker-online-value \.separator,\s*\.tracker-online-value \.total-count\s*\{[^}]*color:#ffffff/);
  });
});

describe('tracker top summary icons use owned cached URLs only', () => {
  test('icons render from server-resolved trackerTopSummaryIcons locals', () => {
    assert.match(src, /src="<%= TTS\.online %>"/);
    assert.match(src, /if \(TTS\.secret\)[\s\S]*src="<%= TTS\.secret %>"/);
    assert.match(src, /if \(TTS\.forgotten\)[\s\S]*src="<%= TTS\.forgotten %>"/);
    assert.match(src, /if \(TTS\.ruby\)[\s\S]*src="<%= TTS\.ruby %>"/);
    assert.match(src, /if \(TTS\.evolved\)[\s\S]*src="<%= TTS\.evolved %>"/);
    assert.match(src, /if \(TTS\.runic\)[\s\S]*src="<%= TTS\.runic %>"/);
  });

  test('no fallback/onerror in top grid markup', () => {
    const block = topGridBlock();
    assert.doesNotMatch(block, /fallback-|onerror=/);
  });

  test('broken-image guard uses .tracker-top-summary-card img selector', () => {
    assert.match(src, /querySelectorAll\('\.tracker-top-summary-card img'\)/);
  });
});

describe('owned top-grid asset cache + resolver', () => {
  const topGrid = require('../src/fishitTrackerTopGridAssets');
  const resolved = topGrid.resolveTopSummaryIcons();

  test('manifest exists with owned public URLs under /public/assets/tracker-top-grid/', () => {
    assert.ok(fs.existsSync(topGrid.MANIFEST_PATH));
    for (const key of ['secret', 'forgotten', 'ruby', 'evolved', 'runic']) {
      assert.ok(resolved[key], `${key} must resolve`);
      assert.match(resolved[key], /^\/public\/assets\/tracker-top-grid\//, `${key} must use owned URL`);
      assert.doesNotMatch(resolved[key], /\/api\/fishit-tracker\//, `${key} must not hotlink API`);
    }
  });

  test('owned files exist on disk for each DB-backed card', () => {
    const manifest = topGrid.loadManifest();
    for (const cardKey of ['secretFish', 'forgottenFish', 'rubyGemstone', 'evolvedEnchantStone', 'runicStone']) {
      const row = manifest.cards[cardKey];
      assert.ok(row && row.ownedAssetPath, cardKey);
      assert.ok(fs.existsSync(path.join(__dirname, '..', row.ownedAssetPath)), row.ownedAssetPath);
      assert.ok(row.sha256 && row.sha256.length >= 32);
    }
  });

  test('secret/forgotten chosen by exact rarity; ruby by name; runic from manual override', () => {
    assert.equal(String(resolved.proof.secret.sourceRarity || 'Secret'), 'Secret');
    assert.equal(String(resolved.proof.forgotten.sourceRarity || 'Forgotten'), 'Forgotten');
    assert.equal(resolved.proof.ruby.sourceName, 'Ruby');
    assert.equal(resolved.proof.runic.sourceType, 'manual_override');
  });

  test('online avatar remains user-uploaded path', () => {
    assert.equal(resolved.online, '/public/img/tracker/online_avatar.png');
  });
});

describe('scoping: legacy/dashboard cards untouched', () => {
  test('dashboard stats still use legacy stat-card tiles', () => {
    assert.match(src, /class="inventory-stats dashboard-stats"/);
  });
});
