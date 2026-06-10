'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('node:vm');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const {
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
} = require('../src/fishitTrackerLoadstring');
const {
  BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER,
  BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');
const { buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');
const trackerRouter = require('../src/fishitTrackerRoutes');
const ejs = require('ejs');

const TRACKER_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

function extractInlineJs(html) {
  const start = html.indexOf('<script>');
  const end = html.indexOf('</script>', start);
  return html.slice(start + 8, end);
}

function countMatches(text, re) {
  const m = text.match(re);
  return m ? m.length : 0;
}

function createMockElement(id, opts = {}) {
  const listeners = {};
  const el = {
    id,
    dataset: {},
    value: opts.value || '',
    textContent: opts.textContent || '',
    innerHTML: '',
    className: '',
    classList: {
      _tokens: new Set(),
      add(...tokens) { tokens.forEach((t) => this._tokens.add(t)); },
      remove(...tokens) { tokens.forEach((t) => this._tokens.delete(t)); },
      toggle(token, force) {
        if (force === true) this._tokens.add(token);
        else if (force === false) this._tokens.delete(token);
        else if (this._tokens.has(token)) this._tokens.delete(token);
        else this._tokens.add(token);
      },
      contains(token) { return this._tokens.has(token); },
    },
    style: {},
    hidden: false,
    disabled: false,
    parentNode: null,
    children: [],
    setAttribute(name, value) { this[name] = value; },
    getAttribute(name) { return this[name] ?? null; },
    appendChild(child) {
      this.children.push(child);
      child.parentNode = this;
      return child;
    },
    remove() {
      if (this.parentNode && this.parentNode.children) {
        this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
      }
    },
    querySelector(sel) {
      if (sel === '.card-head') return createMockElement(`${id}-head`);
      if (sel === '[data-remove-btn]') return createMockElement(`${id}-remove`);
      if (sel === '[data-status-line]') return createMockElement(`${id}-status`);
      if (sel === '[data-card-body]') return createMockElement(`${id}-body`);
      if (sel === '[data-sync-time]') return createMockElement(`${id}-sync`);
      return null;
    },
    querySelectorAll() { return []; },
    closest() { return null; },
    scrollIntoView() {},
    focus() { this.focused = true; },
    select() { this.selected = true; },
    addEventListener(type, fn) { listeners[type] = listeners[type] || []; listeners[type].push(fn); },
    click() { (listeners.click || []).forEach((fn) => fn({ preventDefault() {}, stopPropagation() {}, target: el })); },
    dispatch(type, event) { (listeners[type] || []).forEach((fn) => fn(event)); },
  };
  return el;
}

describe('BLOCKER10ZR inventory buttons + clean copy UI', () => {
  test('build marker is BLOCKER10ZS and wired to deploy marker', () => {
    assert.equal(
      BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER,
      'BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_2026_06_10',
    );
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_MARKER);
    assert.equal(
      BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_MARKER,
      'BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_2026_06_10',
    );
  });

  test('/inventory HTML has single script field and one Copy button', async () => {
    const res = await request(makeApp()).get('/inventory').expect(200);
    assert.match(res.text, /BLOCKER10ZS_GITHUB_CACHE_REQUEST_AND_TRANSCENDED_STONE_LIVE_IMAGE_2026_06_10/);
    assert.match(res.text, /data-inventory-js="pending"/);
    assert.equal(countMatches(res.text, /id="loadstringCode"/g), 1);
    assert.equal(countMatches(res.text, /id="copyBtn"/g), 1);
    assert.doesNotMatch(res.text, /id="copyScriptTextarea"/);
    assert.doesNotMatch(res.text, /id="selectScriptBtn"/);
    assert.doesNotMatch(res.text, /Select script/i);
    assert.match(res.text, new RegExp(CLEAN_TRACKER_LOADSTRING.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.doesNotMatch(res.text, /\/main\/tracker\.lua/);
    assert.match(res.text, /id="addBtn" type="button"/);
    assert.match(res.text, /data-inventory-mode="individual"/);
    assert.match(res.text, /data-inventory-mode="bulk"/);
    assert.match(res.text, /id="usernameInput"/);
    assert.doesNotMatch(res.text, /id="usernameInput" disabled/);
  });

  test('/inventory?apk=1 keeps working controls', async () => {
    const res = await request(makeApp()).get('/inventory?apk=1').expect(200);
    assert.match(res.text, /inventory-apk-embed/);
    assert.match(res.text, /id="addBtn" type="button"/);
    assert.match(res.text, /id="copyBtn"/);
    assert.doesNotMatch(res.text, /selectScriptBtn/);
  });

  test('inline inventory JS parses without HTML-escaped bootstrap', async () => {
    const html = await ejs.renderFile(TRACKER_PATH, buildTrackerPageLocals({ query: {} }), { async: true });
    const js = extractInlineJs(html);
    assert.doesNotMatch(js, /&#34;/);
    assert.doesNotMatch(js, /&#39;/);
    assert.doesNotThrow(() => { new Function(js); });
  });

  test('mock DOM smoke: add player, tabs, copy init without throwing', async () => {
    const html = await ejs.renderFile(TRACKER_PATH, buildTrackerPageLocals({ query: {} }), { async: true });
    const js = extractInlineJs(html);

    const elements = {
      usernameInput: createMockElement('usernameInput'),
      addBtn: createMockElement('addBtn'),
      copyBtn: createMockElement('copyBtn'),
      loadstringCode: createMockElement('loadstringCode', { value: CLEAN_TRACKER_LOADSTRING }),
      copyStatus: createMockElement('copyStatus'),
      usernameError: createMockElement('usernameError'),
      trackerList: createMockElement('trackerList'),
      bulkInventoryPanel: createMockElement('bulkInventoryPanel'),
      bulkInventoryBody: createMockElement('bulkInventoryBody'),
      bulkInventoryHeader: createMockElement('bulkInventoryHeader'),
      noTrackersMsg: createMockElement('noTrackersMsg'),
      summaryText: createMockElement('summaryText'),
    };
    elements.trackerList.style = {};
    elements.noTrackersMsg.style = {};
    const body = createMockElement('body');
    body.setAttribute('data-inventory-js', 'pending');
    const documentElement = createMockElement('html');
    documentElement.getAttribute = (name) => (name === 'data-render-build' ? '' : null);

    const tabIndividual = createMockElement('tab-individual');
    tabIndividual.setAttribute('data-inventory-mode', 'individual');
    tabIndividual.classList.add('is-active');
    const tabBulk = createMockElement('tab-bulk');
    tabBulk.setAttribute('data-inventory-mode', 'bulk');

    const sandbox = {
      window: {},
      document: {
        readyState: 'complete',
        documentElement,
        body,
        getElementById(id) { return elements[id] || null; },
        querySelector(sel) {
          if (sel === '[data-bulk-search-input]' || sel === '[data-bulk-search-clear]') return null;
          return null;
        },
        querySelectorAll(sel) {
          if (sel === '[data-inventory-mode]') return [tabIndividual, tabBulk];
          return [];
        },
        createElement() { return createMockElement('dynamic'); },
      },
      localStorage: {
        store: {},
        getItem(k) { return this.store[k] ?? null; },
        setItem(k, v) { this.store[k] = String(v); },
      },
      navigator: { clipboard: { writeText: async () => {} } },
      console,
      setInterval: () => 1,
      clearInterval() {},
      fetch: async () => ({ status: 404, ok: false, json: async () => ({}) }),
      URLSearchParams,
      Math,
      Number,
      String,
      Array,
      Object,
      JSON,
      Date,
      Promise,
      setTimeout,
    };
    sandbox.window = sandbox;
    sandbox.location = { search: '', href: 'http://127.0.0.1/inventory' };
    sandbox.window.location = sandbox.location;

    vm.createContext(sandbox);
    vm.runInContext(js, sandbox, { timeout: 5000 });

    assert.equal(sandbox.window.__fishInventoryUiReady, true);
    assert.equal(body.getAttribute('data-inventory-js'), 'ready');

    elements.usernameInput.value = 'denghub2';
    elements.addBtn.click();
    assert.ok(elements.trackerList.children && elements.trackerList.children.length >= 1);

    tabBulk.click();
    assert.equal(tabBulk.classList.contains('is-active'), true);
    tabIndividual.click();
    assert.equal(tabIndividual.classList.contains('is-active'), true);

    elements.copyBtn.click();
    await Promise.resolve();
    await Promise.resolve();
    assert.ok(
      /Copied!|Copy failed/.test(elements.copyStatus.textContent)
      || elements.copyBtn.classList.contains('copied'),
    );
  });

  test('canonical loadstring uses dist path from central constant', () => {
    assert.match(PROTECTED_DIST_RAW_URL, /\/main\/dist\/tracker\.lua$/);
    assert.equal(CLEAN_TRACKER_LOADSTRING, `loadstring(game:HttpGet("${PROTECTED_DIST_RAW_URL}"))()`);
  });
});
