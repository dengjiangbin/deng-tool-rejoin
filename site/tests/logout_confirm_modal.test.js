'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const trackerRouter = require('../src/fishitTrackerRoutes');

const LAYOUT_PATH = path.join(__dirname, '..', 'views', 'layout.ejs');
const LOGOUT_JS = path.join(__dirname, '..', 'public', 'js', 'logoutConfirm.js');
const LOGOUT_CSS = path.join(__dirname, '..', 'public', 'css', 'logoutConfirm.css');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('logout confirmation modal hotfix', () => {
  test('shared logoutConfirm assets define modal behavior once', () => {
    const js = fs.readFileSync(LOGOUT_JS, 'utf8');
    const css = fs.readFileSync(LOGOUT_CSS, 'utf8');
    assert.match(js, /function openLogoutConfirmModal/);
    assert.match(js, /window\.openLogoutConfirmModal = openLogoutConfirmModal/);
    assert.match(js, /logoutConfirmOverlay/);
    assert.match(js, /Are you sure you want to logout\?/);
    assert.match(js, /event\.preventDefault\(\)/);
    assert.match(js, /event\.key === 'Escape'/);
    assert.match(js, /form\.submit\(\)/);
    assert.match(js, /Logging out\.\.\./);
    assert.match(css, /\.logout-confirm-overlay/);
    assert.match(css, /backdrop-filter: blur\(8px\)/);
    assert.match(css, /\.logout-confirm-submit/);
  });

  test('layout sidebar logout uses shared confirm modal assets and does not submit immediately', () => {
    const layout = fs.readFileSync(LAYOUT_PATH, 'utf8');
    assert.match(layout, /logoutConfirm\.css\?v=<%= assetVersion %>/);
    assert.match(layout, /logoutConfirm\.js\?v=<%= assetVersion %>/);
    assert.match(layout, /class="nav-link logout-link" data-logout-confirm/);
    assert.match(layout, /type="button"/);
    assert.doesNotMatch(layout, /class="nav-link logout-link"[\s\S]*type="submit"/);
  });

  test('inventory page logout uses shared confirm modal assets and does not submit immediately', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, /logoutConfirm\.css\?v=/);
    assert.match(res.text, /logoutConfirm\.js\?v=/);
    assert.match(res.text, /inventory-action-btn--logout" data-logout-confirm/);
    assert.match(res.text, /type="button" class="inventory-action-btn inventory-action-btn--logout"/);
    assert.doesNotMatch(res.text, /type="submit" class="inventory-action-btn inventory-action-btn--logout"/);
  });

  test('logout trigger selectors cover sidebar and inventory logout classes', () => {
    const js = fs.readFileSync(LOGOUT_JS, 'utf8');
    assert.match(js, /\.logout-link/);
    assert.match(js, /\.inventory-action-btn--logout/);
    assert.match(js, /form\[action="\/auth\/logout"\] button/);
  });
});
