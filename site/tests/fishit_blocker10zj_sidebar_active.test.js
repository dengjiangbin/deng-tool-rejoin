'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const LAYOUT_PATH = path.join(__dirname, '..', 'views', 'layout.ejs');
const CSS_PATH = path.join(__dirname, '..', 'public', 'css', 'style.css');

const MENU = [
  { label: 'Dashboard', href: '/dashboard', activePage: 'dashboard' },
  { label: 'My License', href: '/license', activePage: 'license' },
  { label: 'Inventory', href: '/inventory', activePage: 'inventory' },
  { label: 'Stats', href: '/fishit', activePage: 'fishit' },
  { label: 'Download', href: '/download', activePage: 'download' },
];

function sidebarActiveCssBlock(css) {
  const match = css.match(/\/\* Single shared active sidebar style[\s\S]*?\.nav-link\.active \.nav-icon \{[\s\S]*?\}/);
  return match ? match[0] : '';
}

describe('BLOCKER10ZJ sidebar active gradient polish', () => {
  test('sidebar menu order remains Dashboard, My License, Inventory, Stats, Download', () => {
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    const labels = [...layout.matchAll(/<span>(Dashboard|My License|Inventory|Stats|Download)<\/span>/g)].map((m) => m[1]);
    assert.deepEqual(labels, MENU.map((m) => m.label));
    assert.doesNotMatch(layout, /<span>Fish It<\/span>/);
    assert.doesNotMatch(layout, /<span>Rejoin APK<\/span>/);
    assert.doesNotMatch(layout, /<span>Live Tracker<\/span>/);
  });

  test('layout uses shared is-active class for every sidebar route', () => {
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    for (const item of MENU) {
      assert.match(
        layout,
        new RegExp(`locals\\.activePage === '${item.activePage}' \\? 'is-active' : ''`),
        `${item.label} must use is-active`,
      );
    }
    assert.doesNotMatch(layout, /locals\.activePage === '[^']+' \? 'active' : ''/);
  });

  test('active sidebar CSS is centralized and avoids broken gradient edge causes', () => {
    const css = fs.readFileSync(CSS_PATH, 'utf8');
    const block = sidebarActiveCssBlock(css);
    assert.ok(block, 'shared active sidebar CSS block must exist');
    assert.match(block, /\.nav-link\.is-active,\s*\n\.nav-link\.active/);
    assert.match(block, /border-radius:\s*12px/);
    assert.match(block, /overflow:\s*hidden/);
    assert.match(block, /background-clip:\s*padding-box/);
    assert.match(block, /\.nav-link\.is-active > \*,\s*\n\.nav-link\.active > \*[\s\S]*z-index:\s*1/);
    assert.match(block, /\.nav-link\.is-active \.nav-icon,\s*\n\.nav-link\.active \.nav-icon[\s\S]*background:\s*transparent/);
    assert.doesNotMatch(block, /clip-path/i);
    assert.doesNotMatch(block, /mask-image/i);
    assert.doesNotMatch(block, /::before|::after/);
    assert.doesNotMatch(block, /margin:\s*-/);
  });

  test('sidebar CSS has no clip-path or mask-image on active nav selectors', () => {
    const css = fs.readFileSync(CSS_PATH, 'utf8');
    const activeBlocks = [...css.matchAll(/\.nav-link\.(?:is-active|active)[^{]*\{[^}]*\}/g)].map((m) => m[0]);
    assert.ok(activeBlocks.length >= 2, 'expected active nav-link rules');
    for (const block of activeBlocks) {
      assert.doesNotMatch(block, /clip-path/i, block);
      assert.doesNotMatch(block, /mask-image/i, block);
    }
  });

  test('each menu route template marks only its own row active via is-active', () => {
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    for (const item of MENU) {
      const pattern = new RegExp(
        `href="${item.href.replace('/', '\\/')}" class="nav-link <%= locals\\.activePage === '${item.activePage}' \\? 'is-active' : '' %>"`,
      );
      assert.match(layout, pattern, `${item.label} must bind is-active to ${item.activePage}`);
    }
    const activeTernaries = layout.match(/locals\.activePage === '[^']+' \? 'is-active' : ''/g) || [];
    assert.equal(activeTernaries.length, MENU.length, 'each sidebar item must have one is-active ternary');
  });
});
