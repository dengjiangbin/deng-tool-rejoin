#!/usr/bin/env node
/**
 * BLOCKER10ZR: inventory button bindings + clean single-field copy UI validator.
 */
const http = require('http');
const path = require('path');
const ejs = require(path.join(__dirname, '..', 'site', 'node_modules', 'ejs'));
const {
  BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_MARKER,
} = require('../site/src/fishitTrackerBuild');
const {
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
} = require('../site/src/fishitTrackerLoadstring');
const { buildTrackerPageLocals } = require('../site/src/fishitTrackerRoutes');
const trackerPath = path.join(__dirname, '..', 'site', 'views', 'fishit_tracker.ejs');

const SITE_HOST = process.env.TOOL_SITE_HOST || '127.0.0.1';
const SITE_PORT = Number(process.env.TOOL_SITE_PORT || 8791);

function fetchText(urlPath) {
  return new Promise((resolve, reject) => {
    http.get({ hostname: SITE_HOST, port: SITE_PORT, path: urlPath, headers: { Accept: 'text/html' } }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode || 0, body }));
    }).on('error', reject);
  });
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

async function renderInventoryHtml(query = {}) {
  return ejs.renderFile(trackerPath, buildTrackerPageLocals({ query }), { async: true });
}

async function main() {
  const errors = [];

  if (BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_MARKER
    !== 'BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_2026_06_10') {
    errors.push('BLOCKER10ZR marker mismatch in fishitTrackerBuild.js');
  }

  const html = await renderInventoryHtml({});
  const apkHtml = await renderInventoryHtml({ apk: '1' });

  for (const [label, page] of [['inventory', html], ['inventory?apk=1', apkHtml]]) {
    if (!page.includes(BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_MARKER)) {
      errors.push(`${label}: missing BLOCKER10ZR marker`);
    }
    if (!page.includes('data-inventory-js="pending"')) {
      errors.push(`${label}: missing data-inventory-js pending marker on body`);
    }
    if (!page.includes('id="loadstringCode"')) errors.push(`${label}: missing single loadstring field`);
    if (page.includes('id="copyScriptTextarea"')) errors.push(`${label}: duplicate copyScriptTextarea must be removed`);
    if (page.includes('id="selectScriptBtn"')) errors.push(`${label}: Select script button must be removed`);
    if (/Select script/i.test(page)) errors.push(`${label}: Select script text must not appear`);
    if (countMatches(page, /id="copyBtn"/g) !== 1) errors.push(`${label}: must have exactly one Copy button`);
    if (countMatches(page, /id="loadstringCode"/g) !== 1) errors.push(`${label}: must have exactly one loadstring field`);
    if (!page.includes(CLEAN_TRACKER_LOADSTRING)) errors.push(`${label}: missing canonical loadstring`);
    if (page.includes('/main/tracker.lua')) errors.push(`${label}: exposes root raw tracker URL`);
    if (!page.includes('id="addBtn" type="button"')) errors.push(`${label}: addBtn must be type=button`);
    if (!page.includes('data-inventory-mode="individual"')) errors.push(`${label}: missing individual tab`);
    if (!page.includes('data-inventory-mode="bulk"')) errors.push(`${label}: missing bulk tab`);
    if (!page.includes('safeBind(')) errors.push(`${label}: missing safeBind init guard`);
    if (!page.includes('__fishInventoryUiReady')) errors.push(`${label}: missing __fishInventoryUiReady init marker`);
    if (page.includes('&#34;')) errors.push(`${label}: HTML-escaped JS bootstrap still present`);
  }

  try {
    new Function(extractInlineJs(html));
  } catch (e) {
    errors.push(`inventory inline JS parse failed: ${e.message}`);
  }

  let live;
  try {
    live = await fetchText('/inventory');
  } catch (e) {
    errors.push(`live /inventory fetch failed: ${e.message}`);
  }
  if (live) {
    if (live.status !== 200) errors.push(`live /inventory HTTP ${live.status}`);
    if (!live.body.includes(BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_MARKER)) {
      errors.push('live /inventory missing BLOCKER10ZR marker');
    }
    if (live.body.includes('selectScriptBtn')) errors.push('live /inventory still has Select script button');
    if (live.body.includes('copyScriptTextarea')) errors.push('live /inventory still has duplicate script textarea');
    try {
      new Function(extractInlineJs(live.body));
    } catch (e) {
      errors.push(`live /inventory inline JS parse failed: ${e.message}`);
    }
  }

  if (errors.length) {
    console.error('BLOCKER10ZR_INVENTORY_BUTTONS_VALIDATION FAILED');
    for (const err of errors) console.error('  -', err);
    process.exit(1);
  }

  console.log('BLOCKER10ZR_INVENTORY_BUTTONS_VALIDATION OK');
  console.log('  marker:', BLOCKER10ZR_FIX_INVENTORY_BUTTON_BINDINGS_CLEAN_COPY_UI_MARKER);
  console.log('  loadstring URL:', PROTECTED_DIST_RAW_URL);
  console.log('  live /inventory HTTP:', live ? live.status : 'skipped');
}

main().catch((e) => {
  console.error('BLOCKER10ZR_INVENTORY_BUTTONS_VALIDATION FAILED:', e.message);
  process.exit(1);
});
