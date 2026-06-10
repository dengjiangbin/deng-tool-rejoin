'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

const { CLEAN_TRACKER_LOADSTRING } = require('../src/fishitTrackerLoadstring');
const { BLOCKER10ZG_BUILD } = require('../src/fishitTrackerBuild');
const { PUBLIC_API_BUILD, buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');
const trackerRouter = require('../src/fishitTrackerRoutes');

const FINAL_BUILD = 'BLOCKER10ZK_INVENTORY_MOBILE_BULK_APK_2026_06_09';
const PUBLIC_LOADER = CLEAN_TRACKER_LOADSTRING;
const TRACKER_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const MOJIBAKE = '\uFFFD';

function makeTrackerApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZG clean icons + loader script', () => {
  test('build marker is BLOCKER10ZG', () => {
    assert.equal(BLOCKER10ZG_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
    assert.equal(buildTrackerPageLocals().trackerLoadstring, CLEAN_TRACKER_LOADSTRING);
  });

  test('clean loader script loads dist/tracker.lua not raw root tracker.lua', () => {
    assert.equal(CLEAN_TRACKER_LOADSTRING, PUBLIC_LOADER);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /\/main\/tracker\.lua"\)\)\(\)/);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /tracker\.luraph\.lua/);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /tostring/);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /os\.time/);
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /\?t=/);
  });

  test('tracker template has no mojibake replacement character', () => {
    const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
    assert.doesNotMatch(tpl, new RegExp(MOJIBAKE));
  });

  test('/tracker renders clean executor label and unlimited players text', async () => {
    const res = await request(makeTrackerApp()).get('/tracker').expect(200);
    assert.doesNotMatch(res.text, new RegExp(MOJIBAKE));
    assert.match(res.text, /Executor Script &mdash; copy &amp; run in-game/);
    assert.match(res.text, /Track unlimited players simultaneously\./);
    assert.doesNotMatch(res.text, /sessions are saved and restored on page reload/i);
    assert.match(res.text, /loadstring-box__icon/);
    assert.match(res.text, /header__lead-icon/);
  });

  test('/tracker public loader script is clean without tostring or ?t=', async () => {
    const res = await request(makeTrackerApp()).get('/tracker').expect(200);
    assert.match(res.text, /id="loadstringCode"/);
    assert.match(res.text, new RegExp(CLEAN_TRACKER_LOADSTRING.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.doesNotMatch(res.text, /tostring\(os\.time\(\)\)/);
    assert.doesNotMatch(res.text, /\?t=/);
    assert.doesNotMatch(res.text, /Debug loader \(cache-busted\)/);
    assert.doesNotMatch(res.text, /tracker\.luraph\.lua/);
  });

  test('/tracker?debug=global still does not expose raw root loader', async () => {
    const res = await request(makeTrackerApp()).get('/tracker?debug=global').expect(200);
    assert.match(res.text, /dist\/tracker\.lua/);
    assert.doesNotMatch(res.text, /main\/tracker\.lua\?t=/);
    assert.doesNotMatch(res.text, /loadstringDebugBox/);
  });

  test('copy button uses clean script constant not debug loader', async () => {
    const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
    assert.match(tpl, /copyTrackerScript/);
    assert.match(tpl, /fallbackCopyText/);
    assert.match(tpl, /CLEAN_LOADSTRING/);
    assert.doesNotMatch(tpl, /writeText\(document\.getElementById\('loadstringCode'\)\.textContent\)/);
    assert.match(tpl, /Copied!/);
    assert.doesNotMatch(tpl, /\? Copied!/);
    assert.doesNotMatch(tpl, /loadstringDebugBox/);
  });
});
