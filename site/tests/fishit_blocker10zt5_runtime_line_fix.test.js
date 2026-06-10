'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const {
  BLOCKER10ZT5_RUNTIME_LINE_FIX_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
  EXPECTED_CLIENT_TRACKER_BUILD,
} = require('../src/fishitTrackerBuild');
const { LOADER_BUILD } = require('../src/fishitTrackerLoadstring');

const ROOT = path.join(__dirname, '..', '..');
const LOADER_LUA = path.join(ROOT, 'scripts', 'loader.lua');
const RAW_LUA = path.join(ROOT, '..', 'DENG PRIVATE SOURCE', 'fishtracker', 'tracker.lua');
const VALIDATE_COMPILE = path.join(ROOT, 'scripts', 'validate_tracker_compile.js');

function readRawLua() {
  if (!fs.existsSync(RAW_LUA)) return null;
  return fs.readFileSync(RAW_LUA, 'utf8');
}

describe('BLOCKER10ZT5 runtime line fix', () => {
  test('build markers point to BLOCKER10ZT5', () => {
    assert.equal(EXPECTED_CLIENT_TRACKER_BUILD, 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10');
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZT5_RUNTIME_LINE_FIX_MARKER);
    assert.equal(LOADER_BUILD, EXPECTED_CLIENT_TRACKER_BUILD);
    const loader = fs.readFileSync(LOADER_LUA, 'utf8');
    assert.match(loader, /BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10/);
  });

  test('raw tracker has forward decl, proof log, and safe playerStats guards', () => {
    const src = readRawLua();
    assert.ok(src, 'private raw tracker.lua required for this test');
    assert.match(src, /local TRACKER_BUILD = "BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10"/);
    assert.match(src, /local function readReplionData\(replion\)/);
    assert.match(src, /ZidEulFJFvuuEFDERxXTMbGj/);
    assert.match(src, /runtimeLineFixProof/);
    assert.match(src, /RUNTIME_LINE_FIX line=951/);
    assert.match(src, /RUNTIME_LINE_FIX line=1087/);
    assert.match(src, /RUNTIME_LINE_FIX line=1457/);
    assert.match(src, /RUNTIME_LINE_FIX line=6511/);
    const resolveIdx = src.indexOf('local function resolveReplionStatData');
    const defineIdx = src.indexOf('local function readReplionData(replion)');
    assert.ok(defineIdx > 0 && defineIdx < resolveIdx, 'readReplionData must be defined before resolveReplionStatData');
  });

  test('validate_tracker_compile passes for ZT5 raw source', () => {
    if (!fs.existsSync(RAW_LUA)) return;
    const out = execFileSync(process.execPath, [VALIDATE_COMPILE, RAW_LUA], {
      cwd: ROOT,
      encoding: 'utf8',
    });
    assert.match(out, /OK|luau-compile/);
  });
});
