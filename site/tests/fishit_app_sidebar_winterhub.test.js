'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('node:path');

const LAYOUT_PATH = path.join(__dirname, '..', 'views', 'layout.ejs');
const PARTIAL_PATH = path.join(__dirname, '..', 'views', 'partials', 'deng-sidebar-nav.ejs');
const SIDEBAR_CSS_PATH = path.join(__dirname, '..', 'public', 'css', 'app-sidebar.css');

const MENU = [
  { label: 'Dashboard', href: '/dashboard', activePage: 'dashboard' },
  { label: 'Live Tracker', href: '/tracker', activePage: 'tracker' },
  { label: 'My License', href: '/license', activePage: 'license' },
  { label: 'Download', href: '/download', activePage: 'download' },
];

function sidebarActiveCssBlock(css) {
  const match = css.match(/\.sidebar-link\.is-active,\s*\n\.sidebar-link\.active \{[\s\S]*?box-shadow:[^;]+;/);
  return match ? match[0] : '';
}

describe('WinterHUB desktop app sidebar', () => {
  test('layout and tracker include shared deng-sidebar-nav partial', () => {
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    const trackerSource = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'),
      'utf8',
    );
    assert.match(layout, /include\('partials\/deng-sidebar-nav'/);
    assert.match(trackerSource, /include\('partials\/deng-sidebar-nav'/);
    assert.match(layout, /class="sidebar deng-app-sidebar"/);
    assert.match(trackerSource, /class="inventory-sidebar sidebar deng-app-sidebar"/);
  });

  test('sidebar menu order is MAIN Dashboard, Live Tracker; TOOLS My License, Download (no Fish It)', () => {
    const partial = fs.readFileSync(PARTIAL_PATH, 'utf8');
    const labels = [...partial.matchAll(/class="sidebar-link__label">([^<]+)</g)].map((m) => m[1]);
    assert.deepEqual(labels, MENU.map((m) => m.label));
    assert.match(partial, /sidebar-section-label">MAIN</);
    assert.match(partial, /sidebar-section-label">TOOLS</);
    // Fish It has been merged into the tracker Dashboard and removed entirely.
    assert.doesNotMatch(partial, /sidebar-section-label">GAME</);
    assert.doesNotMatch(partial, /class="sidebar-link__label">Fish It</);
    assert.doesNotMatch(partial, /href="\/fishit"/);
  });

  test('partial uses is-active class for every app route link', () => {
    const partial = fs.readFileSync(PARTIAL_PATH, 'utf8');
    for (const item of MENU.filter((m) => m.href !== '/tracker' || false)) {
      if (item.href === '/dashboard' || item.href === '/license' || item.href === '/download') {
        assert.match(
          partial,
          new RegExp(`href="${item.href.replace('/', '\\/')}" class="sidebar-link <%= page === '${item.activePage}' \\? 'is-active' : '' %>"`),
          `${item.label} must use is-active`,
        );
      }
    }
    assert.match(partial, /href="\/tracker" class="sidebar-link <%= page === 'tracker' \? 'is-active' : '' %>"/);
  });

  test('active sidebar CSS is centralized in app-sidebar.css without overflow bugs', () => {
    const css = fs.readFileSync(SIDEBAR_CSS_PATH, 'utf8');
    const block = sidebarActiveCssBlock(css);
    assert.ok(block, 'shared active sidebar CSS block must exist');
    assert.match(block, /border-radius:\s*10px/);
    assert.doesNotMatch(block, /clip-path/i);
    assert.doesNotMatch(block, /mask-image/i);
    assert.doesNotMatch(block, /::before|::after/);
    assert.doesNotMatch(block, /overflow:\s*hidden/);
  });

  test('brand block uses compact premium hierarchy classes', () => {
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    assert.match(layout, /sidebar-brand-block__title/);
    assert.match(layout, /sidebar-brand-block__subtitle/);
    assert.match(layout, /sidebar-divider/);
  });

  test('tracker hides mobile segmented nav on desktop sidebar viewport', () => {
    const trackerSource = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'),
      'utf8',
    );
    const sidebarCss = fs.readFileSync(SIDEBAR_CSS_PATH, 'utf8');
    assert.match(trackerSource, /inventory-main-nav--mobile/);
    assert.match(trackerSource, /data-mobile-tracker-tabs/);
    assert.match(trackerSource, /\.inventory-main-nav--mobile \{ display:none; \}/);
    assert.match(
      trackerSource,
      /@media \(min-width:769px\) \{[\s\S]*?\.inventory-sidebar \.inventory-main-nav--mobile[\s\S]*?display:none !important/,
    );
    assert.match(trackerSource, /@media \(max-width:768px\) \{[\s\S]*?\.inventory-main-nav--mobile \{ display:flex; \}/);
    assert.match(trackerSource, /\.inventory-apk-embed \.inventory-main-nav--mobile \{ display:flex; \}/);
    assert.match(trackerSource, /function syncMobileTrackerNavVisibility\(/);
    assert.match(sidebarCss, /@media \(min-width: 769px\)[\s\S]*\[data-mobile-tracker-tabs\][\s\S]*display: none !important/);
  });

  test('desktop tracker sidebar has one Dashboard and one Live Tracker link, mobile switcher is separate', () => {
    const partial = fs.readFileSync(PARTIAL_PATH, 'utf8');
    const trackerSource = fs.readFileSync(
      path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs'),
      'utf8',
    );
    const desktopDashboard = (partial.match(/sidebar-link__label">Dashboard</g) || []).length;
    const desktopLive = (partial.match(/sidebar-link__label">Live Tracker</g) || []).length;
    assert.equal(desktopDashboard, 1);
    assert.equal(desktopLive, 1);
    const mobileNavMatch = trackerSource.match(
      /<nav class="inventory-main-nav inventory-main-nav--mobile"[\s\S]*?<\/nav>/,
    );
    assert.ok(mobileNavMatch, 'mobile segmented nav must exist');
    assert.match(mobileNavMatch[0], /data-inventory-section="dashboard">Dashboard/);
    assert.match(mobileNavMatch[0], /data-inventory-section="accounts">Live Tracker/);
    const sidebarTopMatch = trackerSource.match(
      /<div class="inventory-sidebar__top">[\s\S]*?<\/div>\s*<div class="inventory-sidebar__spacer"/,
    );
    assert.ok(sidebarTopMatch, 'sidebar top block must exist');
    assert.doesNotMatch(
      sidebarTopMatch[0],
      /sidebar-link__label">Dashboard[\s\S]*inventory-main-nav--mobile[\s\S]*sidebar-link__label">Live Tracker/,
      'desktop sidebar links and mobile switcher must not duplicate labels in one visible stack',
    );
  });
});
