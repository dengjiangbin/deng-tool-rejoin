'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const { RAW_TRACKER_LUA, testIfRawTracker } = require('./helpers/trackerRawSource');

const MARKER = 'PCALL_ERROR_EXPOSE_REQUIRED_UPLOAD_FIX_2026_06_13';
const CANONICAL_URL = 'https://aio.deng.my.id/api/fishit-tracker/update-backpack';

function readLua() {
  return fs.readFileSync(RAW_TRACKER_LUA, 'utf8');
}

describe('FishTracker pcall error expose + requiredOk regression', () => {
  testIfRawTracker('private tracker contains pcall error expose build marker', () => {
    const lua = readLua();
    assert.match(lua, new RegExp(MARKER));
    assert.match(lua, /function HttpDash\.logHttpPcallFail/);
    assert.match(lua, /HTTP_PCALL_FAIL lane=%s url=%s method=%s bodyLen=%d/);
    assert.match(lua, /tostring\(pcallErr\)/);
  });

  testIfRawTracker('uploadOkFromResult returns pcall error string', () => {
    const lua = readLua();
    const fn = lua.match(/function HttpDash\.uploadOkFromResult\([\s\S]*?^end/m);
    assert.ok(fn, 'uploadOkFromResult must exist');
    assert.match(fn[0], /pcall_failed/);
    assert.match(fn[0], /tostring\(pcallErr/);
  });

  testIfRawTracker('required leaderstats fail cannot become requiredOk=1', () => {
    const lua = readLua();
    const syncDashFn = lua.match(/function LiveSafe\.syncPlayerDataDashboard\(\)[\s\S]*?return requiredOk\nend/m);
    assert.ok(syncDashFn, 'syncPlayerDataDashboard must exist');
    assert.doesNotMatch(syncDashFn[0], /requiredOk\s*=\s*leaderstatsUploadOk\s+or\s+\(hasStats\s+and\s+uploadOk\)/);
    assert.match(syncDashFn[0], /requiredOk\s*=\s*hasStats\s+and\s+leaderstatsUploadOk\s+or\s+\(not\s+hasStats\s+and\s+uploadOk\)/);

    const postReq = lua.match(/function HttpDash\.postRequiredLeaderstats\([\s\S]*?^end/m);
    assert.ok(postReq);
    assert.match(postReq[0], /REQUIRED_LEADERSTATS_UPLOAD_OK status=/);
    assert.match(postReq[0], /REQUIRED_LEADERSTATS_UPLOAD_FAIL reason=/);
  });

  testIfRawTracker('inventory success cannot overwrite required leaderstats failure', () => {
    const lua = readLua();
    const syncDashFn = lua.match(/function LiveSafe\.syncPlayerDataDashboard\(\)[\s\S]*?return requiredOk\nend/m);
    assert.ok(syncDashFn);
    assert.match(syncDashFn[0], /leaderstatsUploadOk\s*=\s*statsOk\s*==\s*true/);
    const requiredOkLine = syncDashFn[0].match(/local requiredOk[^\n]+/);
    assert.ok(requiredOkLine, 'requiredOk assignment must exist');
    assert.match(requiredOkLine[0], /hasStats and leaderstatsUploadOk/);
    assert.doesNotMatch(syncDashFn[0], /leaderstatsUploadOk\s+or\s+\(hasStats\s+and\s+uploadOk\)/);
  });

  testIfRawTracker('canonical URL used by required leaderstats and required status', () => {
    const lua = readLua();
    assert.match(lua, new RegExp(CANONICAL_URL.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));

    const postReq = lua.match(/function HttpDash\.postRequiredLeaderstats\([\s\S]*?^end/m);
    assert.ok(postReq);
    assert.match(postReq[0], /Url = TRACKER_URL/);

    const syncStatusFn = lua.match(/function syncStatus\(online, phase, extra\)[\s\S]*?^end/m);
    assert.ok(syncStatusFn);
    assert.match(syncStatusFn[0], /sendDashboardRequest\("required_status"/);
    assert.match(syncStatusFn[0], /Url\s*=\s*TRACKER_URL/);
    assert.match(syncStatusFn[0], /xpcall/);
    assert.match(syncStatusFn[0], /logHttpPcallFail\("required_status"/);
  });

  testIfRawTracker('postRequiredLeaderstats exists and uses xpcall', () => {
    const lua = readLua();
    assert.match(lua, /function HttpDash\.postRequiredLeaderstats/);
    const fn = lua.match(/function HttpDash\.postRequiredLeaderstats\([\s\S]*?^end/m);
    assert.match(fn[0], /xpcall/);
    assert.match(fn[0], /required_leaderstats/);
  });
});
