'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const PARTIAL_PATH = path.join(__dirname, '..', 'views', 'partials', 'deng-sidebar-nav.ejs');
const TEMPLATE_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

describe('tracker auto-poll regression', () => {
  test('live tracker polling stays active on dashboard tab', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.doesNotMatch(
      source,
      /if \(activeInventorySection === 'dashboard'\) \{\s*stopLiveTrackerPolling\(\)/,
    );
    assert.match(source, /function ensureBackgroundPolling/);
    assert.match(source, /ensureBackgroundPolling\(\)/);
    assert.match(source, /startTrackerPollForKey\(key\)/);
    assert.doesNotMatch(
      source,
      /if \(activeInventorySection === 'accounts'\) startTrackerPollForKey\(key\)/,
    );
  });

  test('dashboard tab has 10s background refresh interval', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function startDashboardPolling/);
    assert.match(source, /dashboardPollTimer = setInterval\(\(\) => \{/);
    assert.match(source, /loadDashboardRange\(false\)/);
    assert.match(source, /POLL_MS/);
  });

  test('/tracker defaults to Live Tracker section', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /let activeInventorySection = 'accounts'/);
    assert.match(source, /if \(section === 'dashboard'\)[\s\S]*setInventorySection\('dashboard'\)/);
    assert.match(source, /setInventorySection\('accounts'\)/);

    const template = fs.readFileSync(TEMPLATE_PATH, 'utf8');
    assert.match(template, /inventoryAccountsPanel[^>]*aria-label="Live Tracker"(?![^>]*hidden)/);
    assert.match(template, /inventoryDashboardPanel[^>]*hidden/);
    assert.match(template, /inventory-main-nav__tab is-active" data-inventory-section="accounts"/);

    const partial = fs.readFileSync(PARTIAL_PATH, 'utf8');
    assert.match(partial, /class="sidebar-link is-active"[^>]*data-inventory-section="accounts"/);
    assert.doesNotMatch(partial, /class="sidebar-link is-active"[^>]*data-inventory-section="dashboard"/);
  });

  test('visibility refetch runs regardless of active section', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /document\.visibilityState === 'visible'\) refetchAllAccountStatus\(true\)/);
    assert.doesNotMatch(source, /visibilityState === 'visible' && activeInventorySection === 'accounts'/);
  });
});

describe('per-indicator timer singularity', () => {
  test('indicator 1 always renders one presence sync node', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function ensureSinglePresenceSyncEl/);
    assert.match(source, /data-table-status-sync/);
    assert.match(source, /buildAccountStatusHtml[\s\S]*data-table-status-sync/);
    assert.doesNotMatch(
      source,
      /syncText \? `<span class="accounts-status__text" data-table-status-sync>/,
    );
  });

  test('indicator 2 and 3 dedupe timer nodes on patch', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function ensureSingleStatsSyncSub/);
    assert.match(source, /function ensureSingleInventoryUploadText/);
    assert.match(source, /function tickIndicator1Presence/);
    assert.match(source, /function tickIndicator2Stats/);
    assert.match(source, /function tickIndicator3Inventory/);
    assert.match(source, /let syncTickTimer = null/);
    assert.match(source, /if \(syncTickTimer\) return/);
  });

  test('presence duration keeps minutes and seconds visible', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    const fnBlock = source.match(/function formatPresenceDurationLabel\([^)]*\)\s*\{[\s\S]*?\n  \}/);
    assert.ok(fnBlock, 'formatPresenceDurationLabel must exist');
    const fns = new Function(`
      function pad2(n) { return String(n).padStart(2, '0'); }
      ${fnBlock[0]}
      return { formatPresenceDurationLabel };
    `)();
    assert.equal(fns.formatPresenceDurationLabel(9), '9s');
    assert.equal(fns.formatPresenceDurationLabel(59), '59s');
    assert.equal(fns.formatPresenceDurationLabel(60), '1m 00s');
    assert.equal(fns.formatPresenceDurationLabel(61), '1m 01s');
    assert.equal(fns.formatPresenceDurationLabel(90), '1m 30s');
    assert.equal(fns.formatPresenceDurationLabel(125), '2m 05s');
    assert.notEqual(fns.formatPresenceDurationLabel(60), '1m');
    assert.notEqual(fns.formatPresenceDurationLabel(60), '1M');
  });
});

describe('fish and stone bulk grid totals', () => {
  test('item grid shows summed Enchant Stones and Totem titles', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');
    assert.match(source, /function patchItemGrid[\s\S]*Item Grid/);
    assert.match(source, /function patchStonesSubgrid[\s\S]*Enchant Stones \(\$\{formatQuantity\(stoneTotal\)\}\)/);
    assert.match(source, /function patchTotemsSubgrid[\s\S]*Totem \(\$\{formatQuantity\(totemTotal\)\}\)/);
    assert.match(source, /function patchItemsGrid[\s\S]*Fishes \(\$\{formatQuantity\(fishTotal\)\}\)/);
  });
});
