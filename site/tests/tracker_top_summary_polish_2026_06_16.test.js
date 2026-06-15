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

describe('tracker top summary card layout (screenshot polish)', () => {
  test('inventoryStats uses dedicated tracker-top-summary grid/card classes', () => {
    assert.match(src, /tracker-top-summary-grid" id="inventoryStats"/);
    assert.match(src, /class="tracker-top-summary-card tracker-top-summary-card--online"/);
    assert.match(src, /class="tracker-top-summary-card tracker-top-summary-card--stones"/);
  });

  test('cards are centered with icon, label, value hierarchy', () => {
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*align-items:center/);
    assert.match(src, /\.tracker-top-summary-icon\s*\{/);
    assert.match(src, /\.tracker-top-summary-label\s*\{/);
    assert.match(src, /\.tracker-top-summary-value\s*\{/);
  });

  test('card background/border/radius match screenshot spec', () => {
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*background:#17171d/);
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*border-radius:26px/);
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*border:1px solid rgba\(255,255,255,0\.08\)/);
    assert.match(src, /\.tracker-top-summary-card\s*\{[^}]*min-height:176px/);
  });

  test('grid uses exactly two columns on desktop', () => {
    assert.match(src, /#inventoryStats\.tracker-top-summary-grid\s*\{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  });

  test('mobile (<=640px) collapses to one column', () => {
    assert.match(src, /@media \(max-width:640px\)[\s\S]*#inventoryStats\.tracker-top-summary-grid[\s\S]*grid-template-columns:1fr/);
  });
});

describe('tracker top summary typography + colors', () => {
  test('label is muted gray and not uppercase', () => {
    assert.match(src, /\.tracker-top-summary-label\s*\{[^}]*color:rgba\(255,255,255,0\.48\)/);
    assert.match(src, /\.tracker-top-summary-label\s*\{[^}]*font-size:18px/);
    assert.doesNotMatch(src, /\.tracker-top-summary-label\s*\{[^}]*text-transform:uppercase/);
  });

  test('online count is green with !important, slash/total white', () => {
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

describe('tracker top summary icons use real DB assets (no fallback/custom)', () => {
  test('all icons render from server-resolved trackerTopSummaryIcons locals', () => {
    assert.match(src, /typeof trackerTopSummaryIcons !== 'undefined'/);
    assert.match(src, /src="<%= TTS\.online %>"/);
    assert.match(src, /if \(TTS\.evolved\)[\s\S]*src="<%= TTS\.evolved %>"/);
    assert.match(src, /if \(TTS\.secret\)[\s\S]*src="<%= TTS\.secret %>"/);
    assert.match(src, /if \(TTS\.forgotten\)[\s\S]*src="<%= TTS\.forgotten %>"/);
    assert.match(src, /if \(TTS\.ruby\)[\s\S]*src="<%= TTS\.ruby %>"/);
  });

  test('no emoji/svg-crystal/fallback placeholder in the top summary markup', () => {
    const start = src.indexOf('tracker-top-summary-grid" id="inventoryStats"');
    const block = src.slice(start, start + 3600);
    assert.match(block, /tracker-top-summary-card--ruby/, 'slice should cover all 5 cards');
    assert.doesNotMatch(block, /fallback-secret\.svg|fallback-forgotten\.svg|fallback-fish\.svg/);
    assert.doesNotMatch(block, /onerror=/);
    assert.doesNotMatch(block, /&#x1F/); // no emoji entities
  });

  test('detection-only broken-image guard exists (logs, never swaps)', () => {
    assert.match(src, /function ftAuditTopSummaryImages/);
    assert.match(src, /naturalWidth === 0/);
    assert.match(src, /console\.error\('\[tracker-top-summary\]/);
    // guard must not assign a fallback src
    const guard = src.slice(src.indexOf('function ftAuditTopSummaryImages'), src.indexOf('function ftAuditTopSummaryImages') + 1200);
    assert.doesNotMatch(guard, /\.src\s*=/);
  });
});

describe('top summary icon resolver picks real cached DB assets', () => {
  const icons = require('../src/fishitTrackerTopSummaryIcons');
  const resolved = icons.resolveTopSummaryIcons();

  test('online uses the user-uploaded avatar path', () => {
    assert.equal(resolved.online, '/public/img/tracker/online_avatar.png');
    assert.ok(fs.existsSync(path.join(__dirname, '..', 'public', 'img', 'tracker', 'online_avatar.png')));
  });

  test('evolved/secret/forgotten/ruby resolve to real existing assets', () => {
    for (const key of ['evolved', 'secret', 'forgotten', 'ruby']) {
      assert.ok(resolved[key], `${key} must resolve a real DB asset URL`);
      assert.doesNotMatch(resolved[key], /fallback|placeholder|missing|data:|\.svg/i, `${key} must not be a fallback/placeholder`);
    }
  });

  test('secret/forgotten chosen by exact rarity, ruby by name', () => {
    assert.equal(String(resolved.proof.secret.rarity || 'Secret'), 'Secret');
    assert.ok(resolved.proof.secret.name && resolved.proof.secret.assetId);
    assert.ok(resolved.proof.forgotten.name && resolved.proof.forgotten.assetId);
    assert.equal(resolved.proof.ruby.name, 'Ruby');
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
