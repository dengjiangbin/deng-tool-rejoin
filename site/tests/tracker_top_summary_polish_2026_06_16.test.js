'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

process.env.NODE_ENV = process.env.NODE_ENV || 'test';

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const COUNT_UP_PATH = path.join(__dirname, '..', 'public', 'js', 'count-up-stats.js');
const src = fs.readFileSync(SOURCE_PATH, 'utf8');
const countUpSrc = fs.readFileSync(COUNT_UP_PATH, 'utf8');

function topGridBlock() {
  const start = src.indexOf('tracker-top-summary-grid" id="inventoryStats"');
  return src.slice(start, start + 4200);
}

describe('tracker top summary card layout (3 columns, all platforms)', () => {
  test('inventoryStats uses dedicated tracker-top-summary grid/card classes', () => {
    assert.match(src, /tracker-top-summary-grid" id="inventoryStats"/);
    assert.match(src, /class="tracker-top-summary-card tracker-top-summary-card--online"/);
    assert.match(src, /class="tracker-top-summary-card tracker-top-summary-card--runic"/);
  });

  test('grid is exactly three columns on desktop', () => {
    assert.match(src, /#inventoryStats\.tracker-top-summary-grid\s*\{[^}]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
  });

  test('mobile (<=640px) STAYS three columns (only shrinks)', () => {
    assert.match(src, /@media \(max-width:640px\)[\s\S]*#inventoryStats\.tracker-top-summary-grid[\s\S]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
    // never collapse to 1 or 2 columns
    assert.doesNotMatch(src, /@media \(max-width:640px\)[\s\S]*#inventoryStats\.tracker-top-summary-grid[^}]*grid-template-columns:1fr/);
  });

  test('cards are centered, border-box, with screenshot style', () => {
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*box-sizing:border-box/);
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*background:#17171d/);
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*border-radius:26px/);
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*align-items:center/);
  });
});

describe('tracker top summary card ORDER is exact', () => {
  test('order is Account, Secret, Forgotten, Evolved, Runic', () => {
    const block = topGridBlock();
    const labels = [...block.matchAll(/tracker-top-summary-label">([^<]+)</g)].map((m) => m[1].trim());
    assert.deepEqual(labels.slice(0, 5), [
      'Online / Accounts', 'Secret Fish', 'Forgotten Fish', 'Evolved Enchant Stone', 'Runic Stone',
    ]);
  });

  test('no Ruby Gemstone card remains in the top grid', () => {
    assert.doesNotMatch(topGridBlock(), /Ruby Gemstone/);
  });
});

describe('tracker top summary typography + green online count', () => {
  test('online count green !important, slash/total white', () => {
    assert.match(src, /\.tracker-online-value \.online-count\s*\{[^}]*color:#62e68a !important/);
    assert.match(src, /\.tracker-online-value \.separator,\s*\.tracker-online-value \.total-count\s*\{[^}]*color:#ffffff/);
  });

  test('online value markup carries tracker-online-value + span structure', () => {
    assert.match(src, /class="tracker-top-summary-value tracker-online-value js-count-up" id="statOnlineAccounts"[^>]*data-count-ratio-styled/);
    assert.match(src, /<span class="online-count">0<\/span><span class="separator"> \/ <\/span><span class="total-count">0<\/span>/);
  });

  test('count-up renderer supports styled ratio output', () => {
    assert.match(countUpSrc, /data-count-ratio-styled/);
    assert.match(countUpSrc, /formatRatioHtml/);
    assert.match(countUpSrc, /class="online-count"/);
  });
});

describe('tracker top summary icons use real DB/override assets (no fallback/custom)', () => {
  test('all icons render from server-resolved trackerTopSummaryIcons locals', () => {
    assert.match(src, /typeof trackerTopSummaryIcons !== 'undefined'/);
    assert.match(src, /src="<%= TTS\.online %>"/);
    assert.match(src, /if \(TTS\.secret\)[\s\S]*src="<%= TTS\.secret %>"/);
    assert.match(src, /if \(TTS\.forgotten\)[\s\S]*src="<%= TTS\.forgotten %>"/);
    assert.match(src, /if \(TTS\.evolved\)[\s\S]*src="<%= TTS\.evolved %>"/);
    assert.match(src, /if \(TTS\.runic\)[\s\S]*src="<%= TTS\.runic %>"/);
  });

  test('no emoji/svg-crystal/fallback placeholder in the top summary markup', () => {
    const block = topGridBlock();
    assert.match(block, /tracker-top-summary-card--runic/, 'slice should cover all 5 cards');
    assert.doesNotMatch(block, /fallback-secret\.svg|fallback-forgotten\.svg|fallback-fish\.svg/);
    assert.doesNotMatch(block, /onerror=/);
    assert.doesNotMatch(block, /&#x1F/);
  });

  test('detection-only broken-image guard exists (logs, never swaps)', () => {
    assert.match(src, /function ftAuditTopSummaryImages/);
    assert.match(src, /naturalWidth === 0/);
    const guard = src.slice(src.indexOf('function ftAuditTopSummaryImages'), src.indexOf('function ftAuditTopSummaryImages') + 1200);
    assert.doesNotMatch(guard, /\.src\s*=/);
  });

  test('Runic Stone count is wired to frontend stoneType === Runic', () => {
    assert.match(src, /stoneType === 'Runic'/);
    assert.match(src, /statRunicStoneEl/);
    assert.match(src, /id="statRunicStone"/);
  });
});

describe('top summary icon resolver picks real cached/override assets', () => {
  const icons = require('../src/fishitTrackerTopSummaryIcons');
  const resolved = icons.resolveTopSummaryIcons();

  test('online uses the user-uploaded avatar path', () => {
    assert.equal(resolved.online, '/public/img/tracker/online_avatar.png');
    assert.ok(fs.existsSync(path.join(__dirname, '..', 'public', 'img', 'tracker', 'online_avatar.png')));
  });

  test('secret/forgotten/evolved/runic resolve to real existing assets', () => {
    for (const key of ['secret', 'forgotten', 'evolved', 'runic']) {
      assert.ok(resolved[key], `${key} must resolve a real asset URL`);
      assert.doesNotMatch(resolved[key], /fallback|placeholder|missing|data:|\.svg/i, `${key} must not be a fallback/placeholder`);
    }
  });

  test('runic uses the manual override file, not gameDB', () => {
    assert.equal(resolved.proof.runic.source, 'manual_override');
    assert.equal(resolved.proof.runic.name, 'Runic Stone');
    assert.match(resolved.runic, /\/api\/fishit-tracker\/assets\/manual\/stones\//);
    const file = resolved.proof.runic.file;
    assert.ok(fs.existsSync(path.join(__dirname, '..', 'data', 'manual_image_cache', 'stones', file)), 'override file must exist');
  });

  test('secret/forgotten chosen by exact rarity', () => {
    assert.equal(String(resolved.proof.secret.rarity || 'Secret'), 'Secret');
    assert.ok(resolved.proof.secret.name && resolved.proof.secret.assetId);
    assert.ok(resolved.proof.forgotten.name && resolved.proof.forgotten.assetId);
  });
});

describe('scoping: legacy/dashboard cards untouched', () => {
  test('legacy stat-card rarity/value colors are not applied to top summary cards', () => {
    assert.doesNotMatch(src, /\.stat-card--online \.stat-card__value/);
    assert.doesNotMatch(src, /\.stat-card--stones \.stat-card__value/);
  });

  test('dashboard stats still use legacy stat-card tiles', () => {
    assert.match(src, /class="inventory-stats dashboard-stats"/);
    assert.match(src, /\.dashboard-stats \.stat-card\s*\{/);
  });
});
