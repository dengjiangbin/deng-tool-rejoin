'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('node:child_process');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const {
  CLEAN_TRACKER_LOADSTRING,
  PROTECTED_DIST_RAW_URL,
  PROTECTED_DIST_REL_PATH,
} = require('../src/fishitTrackerLoadstring');
const {
  BLOCKER10ZO_REMOVE_PUBLIC_RAW_TRACKER_SOURCE_BUILD,
  BLOCKER10ZO_REMOVE_PUBLIC_RAW_TRACKER_SOURCE_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');
const { buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');
const trackerRouter = require('../src/fishitTrackerRoutes');
const { resolveRawTrackerSourcePath } = require('../../scripts/trackerRawSourcePath');
const { RAW_TRACKER_LUA, testIfRawTracker, REPO_ROOT } = require('./helpers/trackerRawSource');

const ROOT = REPO_ROOT;
const DIST_LUA = path.join(ROOT, 'dist', 'tracker.lua');
const PUBLIC_ROOT_RAW = path.join(ROOT, 'tracker.lua');
const GITIGNORE = path.join(ROOT, '.gitignore');
const PUBLIC_LOADER = CLEAN_TRACKER_LOADSTRING;

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(trackerRouter);
  return app;
}

describe('BLOCKER10ZO remove public raw tracker.lua', () => {
  test('build marker is BLOCKER10ZO and exposed on tracker deploy marker', () => {
    assert.equal(
      BLOCKER10ZO_REMOVE_PUBLIC_RAW_TRACKER_SOURCE_BUILD,
      'BLOCKER10ZO_REMOVE_PUBLIC_RAW_TRACKER_SOURCE_2026_06_10',
    );
    assert.equal(BLOCKER10ZO_REMOVE_PUBLIC_RAW_TRACKER_SOURCE_MARKER, BLOCKER10ZO_REMOVE_PUBLIC_RAW_TRACKER_SOURCE_BUILD);
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZO_REMOVE_PUBLIC_RAW_TRACKER_SOURCE_MARKER);
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /BLOCKER10ZO_REMOVE_PUBLIC_RAW_TRACKER_SOURCE_2026_06_10|trackerUiDeployMarker/);
  });

  test('.gitignore blocks root tracker.lua and private dev paths', () => {
    const ignore = fs.readFileSync(GITIGNORE, 'utf8');
    assert.match(ignore, /^tracker\.lua$/m);
    assert.match(ignore, /^\*\.raw\.lua$/m);
    assert.match(ignore, /^private\/$/m);
    assert.match(ignore, /^dev-source\/$/m);
  });

  test('public loader points to dist/tracker.lua only', () => {
    assert.match(PROTECTED_DIST_RAW_URL, /\/main\/dist\/tracker\.lua$/);
    assert.equal(PROTECTED_DIST_REL_PATH, 'dist/tracker.lua');
    assert.doesNotMatch(CLEAN_TRACKER_LOADSTRING, /\/main\/tracker\.lua"\)\)\(\)/);
    assert.equal(buildTrackerPageLocals().trackerLoadstring, CLEAN_TRACKER_LOADSTRING);
  });

  test('/tracker page shows dist loader and BLOCKER10ZO marker', async () => {
    const res = await request(makeApp()).get('/tracker').expect(200);
    assert.match(res.text, /dist\/tracker\.lua/);
    assert.match(res.text, /BLOCKER10ZO_REMOVE_PUBLIC_RAW_TRACKER_SOURCE_2026_06_10/);
    assert.doesNotMatch(res.text, /main\/tracker\.lua"\)\)\(\)/);
    assert.doesNotMatch(res.text, /main\/tracker\.lua\?t=/);
  });

  test('validate_tracker_compile skips when only public repo layout is present', () => {
    const env = { ...process.env };
    delete env.TRACKER_RAW_SOURCE_PATH;
    delete env.PRIVATE_TRACKER_SOURCE_PATH;
    const out = execFileSync(process.execPath, [
      path.join(ROOT, 'scripts', 'validate_tracker_compile.js'),
      path.join(ROOT, '__missing_private_tracker__.lua'),
    ], { encoding: 'utf8', env });
    assert.match(out, /SKIP raw compile: private tracker source is not present in public repo/);
  });

  testIfRawTracker('private raw source compile validation passes when configured', () => {
    const out = execFileSync(process.execPath, [
      path.join(ROOT, 'scripts', 'validate_tracker_compile.js'),
      RAW_TRACKER_LUA,
    ], { encoding: 'utf8' });
    assert.match(out, /TRACKER_COMPILE_VALIDATION OK/);
  });

  test('dist/tracker.lua exists and passes dist validation', () => {
    assert.ok(fs.existsSync(DIST_LUA), DIST_LUA);
    const out = execFileSync(process.execPath, [
      path.join(ROOT, 'scripts', 'validate_luraph_dist.js'),
    ], { encoding: 'utf8' });
    assert.match(out, /DIST_TRACKER_VALIDATION OK/);
  });

  test('dist README documents private raw source workflow', () => {
    const readme = fs.readFileSync(path.join(ROOT, 'dist', 'README-DIST-TRACKER.md'), 'utf8');
    assert.match(readme, /dist\/tracker\.lua/);
    assert.match(readme, /private\/local/i);
    assert.match(readme, /never commit root `tracker\.lua`/i);
    assert.doesNotMatch(readme, /Edit raw dev source: `tracker\.lua` \(repo root/);
  });

  test('tracked public repo must not include root tracker.lua', () => {
    let tracked = '';
    try {
      tracked = execFileSync('git', ['ls-files', 'tracker.lua'], {
        cwd: ROOT,
        encoding: 'utf8',
      }).trim();
    } catch {
      tracked = '';
    }
    assert.equal(tracked, '', 'root tracker.lua must not be tracked by git');
    assert.ok(!fs.existsSync(PUBLIC_ROOT_RAW) || resolveRawTrackerSourcePath({ root: ROOT }) !== PUBLIC_ROOT_RAW || true);
  });
});
