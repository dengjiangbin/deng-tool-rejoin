'use strict';

// 2026-06-16, reworked 2026-06-19 (server-timestamp freshness P0):
// the visible Status / leaderstats / inventory timers follow the REAL per-lane
// SERVER upload/snapshot timestamp (the read API's identity-gated lastReal*
// fields), computed as now - serverTimestamp. They are NOT driven by frontend
// receive time, page load, login, poll, or render. The lane timestamp advances
// on every FRESH successful upload regardless of whether fish/item/stat content
// changed, so an online-but-idle user stays fresh; an offline user keeps aging
// from its last real upload and never resets on a website refresh / new device.

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE_PATH = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');
const readSource = () => fs.readFileSync(SOURCE_PATH, 'utf8');

// Extract the formatAgeAgo helpers under a controllable clock for raw format
// assertions ("1s ago" / "1m 2s ago" / "1H 2m ago" / "1D ago" spec).
function makeAgeEnv(source) {
  const open = source.indexOf('  function formatAgeAgo(ms) {');
  const close = source.indexOf('  function syncAgeSeconds(timestamp) {');
  assert.ok(open > 0 && close > open, 'formatAgeAgo helper block missing from source');
  const block = source.slice(open, close);
  const clock = { now: 0 };
  const sandbox = { Math, Number, Date: { now: () => clock.now } };
  const script = `(function(){\n${block}\n  return { formatAgeAgo, formatAgeAgoSeconds };\n})()`;
  const api = vm.runInNewContext(script, sandbox, { filename: 'age-helpers.js' });
  return { api, setNow: (ms) => { clock.now = ms; } };
}

describe('visible timer wiring — derives from real server lane timestamps', () => {
  const src = readSource();
  const executableLines = (fn) => fn.split('\n').filter((l) => /^\s*[a-zA-Z]/.test(l) && !/^\s*\/\//.test(l));

  test('formatPresenceStatusText derives from backendPresenceAgeSeconds (server status ts), not frontend receive', () => {
    const fn = src.match(/function formatPresenceStatusText\(entry\) \{[\s\S]*?\n  \}/)[0];
    assert.match(fn, /return formatBackendAgeText\(backendPresenceAgeSeconds\(entry\)\);/);
    const body = executableLines(fn).join('\n');
    assert.doesNotMatch(body, /formatFrontendRefreshAgeText/);
    assert.doesNotMatch(body, /_frontendRefreshAt/);
  });

  test('formatStatsUploadDurationText derives from backendStatsAgeSeconds (server leaderstats ts)', () => {
    const fn = src.match(/function formatStatsUploadDurationText\(entry\) \{[\s\S]*?\n  \}/)[0];
    assert.match(fn, /return formatBackendAgeText\(backendStatsAgeSeconds\(entry\)\);/);
    const body = executableLines(fn).join('\n');
    assert.doesNotMatch(body, /formatLeaderstatsRefreshAgeText/);
  });

  test('formatEntrySyncStatusText derives from backendInventoryAgeSeconds (server inventory ts)', () => {
    const fn = src.match(/function formatEntrySyncStatusText\(entry\) \{[\s\S]*?\n  \}/)[0];
    assert.match(fn, /return formatBackendAgeText\(backendInventoryAgeSeconds\(entry\)\);/);
    const body = executableLines(fn).join('\n');
    assert.doesNotMatch(body, /formatInventoryRefreshAgeText/);
  });

  test('formatBackendAgeText renders server age seconds via formatAgeAgoSeconds ("X ago")', () => {
    const fn = src.match(/function formatBackendAgeText\(ageSeconds\) \{[\s\S]*?\n  \}/)[0];
    assert.match(fn, /return formatAgeAgoSeconds\(Math\.max\(1, Math\.floor\(ageSeconds\)\)\);/);
  });
});

describe('freshness source of truth — server timestamps only (no page-load reset)', () => {
  const src = readSource();

  test('backend*AgeSeconds read the read-API real lane timestamps (_auth.lastReal*)', () => {
    const presence = src.match(/function backendPresenceAgeSeconds\(entry\) \{[\s\S]*?\n  \}/)[0];
    assert.match(presence, /_auth\.lastRealStatusAt/);
    const stats = src.match(/function backendStatsAgeSeconds\(entry\) \{[\s\S]*?\n  \}/)[0];
    assert.match(stats, /_auth\.lastRealLeaderstatsAt/);
    const inv = src.match(/function backendInventoryAgeSeconds\(entry\) \{[\s\S]*?\n  \}/)[0];
    assert.match(inv, /_auth\.lastRealInventoryAt/);
  });

  test('authAgeSecondsFromTs computes age = corrected server clock - serverTimestamp (never stores/resets a base)', () => {
    const fn = src.match(/function authAgeSecondsFromTs\(ts\) \{[\s\S]*?\n  \}/)[0];
    // Uses Date.now() only to subtract the parsed server timestamp — it is a pure
    // age calculation, so a refresh / new device / login can never reset it.
    assert.match(fn, /parseServerTimeMs\(ts\)/);
    assert.match(fn, /correctedClientNowMs\(\) - ms/);
    assert.doesNotMatch(fn, /_frontendRefreshAt/);
  });

  test('online idle user (unchanged content) stays fresh because the server lane ts advanced', () => {
    const names = [
      'authAgeSecondsFromTs', 'backendPresenceAgeSeconds', 'backendStatsAgeSeconds',
      'backendInventoryAgeSeconds', 'formatBackendAgeText',
      'formatPresenceStatusText', 'formatStatsUploadDurationText', 'formatEntrySyncStatusText',
    ];
    const blocks = names.map((name) => src.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`))[0]);
    const fns = new Function(`
      let trackerServerClockOffsetMs = 0;
      function parseServerTimeMs(value){
        if (value == null || value === '') return null;
        const ms = Date.parse(String(value));
        return Number.isFinite(ms) ? ms : null;
      }
      function correctedClientNowMs(){ return Date.now() + trackerServerClockOffsetMs; }
      function formatAgeAgo(ms){const t=Math.max(0,Math.floor(Number(ms)/1000));if(t<60)return Math.max(1,t)+'s ago';if(t<3600){const m=Math.floor(t/60),s=t%60;return s>0?(m+'m '+s+'s ago'):(m+'m ago');}if(t<86400){const h=Math.floor(t/3600),m=Math.floor((t%3600)/60);return m>0?(h+'h '+m+'m ago'):(h+'h ago');}return Math.floor(t/86400)+'d ago';}
      function formatAgeAgoSeconds(secs){if(secs==null||secs==='')return '';const n=Number(secs);if(!Number.isFinite(n)||n<0)return '';return formatAgeAgo(n*1000);}
      function liveSecondsSinceStatusSuccess(){return null;}
      function entryStatusSuccessTimestamp(){return null;}
      function syncAgeSeconds(){return null;}
      function liveSecondsSinceStatsSuccess(){return null;}
      function liveSecondsSinceInventorySuccess(){return null;}
      ${blocks.join('\n')}
      return { formatPresenceStatusText, formatStatsUploadDurationText, formatEntrySyncStatusText };
    `)();
    const now = Date.now();
    // Lane timestamps advanced ~2s ago (a fresh successful upload), even though the
    // fish/item/stat CONTENT did not change. The timer must read ~2s, not stale.
    const entry = {
      _auth: {
        lastRealStatusAt: new Date(now - 2000).toISOString(),
        lastRealLeaderstatsAt: new Date(now - 2000).toISOString(),
        lastRealInventoryAt: new Date(now - 2000).toISOString(),
      },
    };
    assert.equal(fns.formatPresenceStatusText(entry), '2s ago');
    assert.equal(fns.formatStatsUploadDurationText(entry), '2s ago');
    assert.equal(fns.formatEntrySyncStatusText(entry), '2s ago');
  });
});

describe('format contract (s/m/H/D + " ago")', () => {
  test('exact "X ago" formatting at every range boundary (s / m / H / D)', () => {
    const { api } = makeAgeEnv(readSource());
    assert.equal(api.formatAgeAgo(1000), '1s ago');
    assert.equal(api.formatAgeAgo(11_000), '11s ago');
    assert.equal(api.formatAgeAgo(62_000), '1m 2s ago');
    assert.equal(api.formatAgeAgo(60_000), '1m ago');
    assert.equal(api.formatAgeAgo(3720_000), '1H 2m ago');
    assert.equal(api.formatAgeAgo(3600_000), '1H ago');
    assert.equal(api.formatAgeAgo(86400_000), '1D ago');
  });

  test('null / undefined / negative seconds render blank (no fake "1s")', () => {
    const { api } = makeAgeEnv(readSource());
    assert.equal(api.formatAgeAgoSeconds(null), '');
    assert.equal(api.formatAgeAgoSeconds(undefined), '');
    assert.equal(api.formatAgeAgoSeconds(-5), '');
  });
});

describe('dot truth is backend status age only (independent of the visible timer)', () => {
  const src = readSource();

  test('isTrackerAccountOnline branches on _auth.isOnline FIRST (read-API serve-time)', () => {
    const fn = src.match(/function isTrackerAccountOnline\(entry, nowMs\) \{[\s\S]*?\n  \}/)[0];
    assert.match(fn, /entry\._auth && typeof entry\._auth\.isOnline === 'boolean'/);
  });

  test('the dot path never reads frontend-receive timer fields', () => {
    const fn = src.match(/function isTrackerAccountOnline\(entry, nowMs\) \{[\s\S]*?\n  \}/)[0];
    assert.doesNotMatch(fn, /_frontendRefreshAt/);
    assert.doesNotMatch(fn, /_leaderstatsFrontendRefreshAt/);
    assert.doesNotMatch(fn, /_inventoryFrontendRefreshAt/);
  });
});

describe('server lane timestamps are not a localStorage-backed freshness source', () => {
  const src = readSource();
  for (const field of ['lastRealStatusAt', 'lastRealLeaderstatsAt', 'lastRealInventoryAt']) {
    test(`${field} freshness is not read from / written to localStorage`, () => {
      assert.ok(!new RegExp(`localStorage[\\s\\S]{0,120}${field}`).test(src));
      assert.ok(!new RegExp(`${field}[\\s\\S]{0,120}localStorage`).test(src));
    });
  }
});
