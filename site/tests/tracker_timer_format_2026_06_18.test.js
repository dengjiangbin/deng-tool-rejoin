'use strict';

/**
 * Timer formatting regression (2026-06-18).
 *
 * Requirement: the single canonical "<age> ago" formatter (shared by the status,
 * leaderstats and inventory timers) must render at most TWO units and drop any
 * unnecessary zero unit:
 *   seconds:       "1s ago" .. "59s ago"
 *   minutes+secs:  "1m 2s ago", "6m 30s ago"  (and "5m ago" when secs == 0)
 *   hours+minutes: "1H 2m ago", "5H 10m ago"  (and "2H ago" when mins == 0)
 *   days only:     "1D ago", "2D ago"
 *
 * The formatter lives inside the (browser) tracker template, so we extract its
 * body from the source of truth and evaluate it in isolation — this guards the
 * exact text that ships, not a hand-copied duplicate.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SRC = path.join(__dirname, '..', 'src', 'inventory', 'fishit_tracker.source.ejs');

function loadFormatter() {
  const src = fs.readFileSync(SRC, 'utf8');
  const start = src.indexOf('function formatAgeAgo(ms) {');
  assert.ok(start > -1, 'formatAgeAgo must exist in the tracker source');
  // Walk braces to capture the whole function body.
  let i = src.indexOf('{', start);
  let depth = 0;
  let end = -1;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') { depth -= 1; if (depth === 0) { end = i + 1; break; } }
  }
  const fnSrc = src.slice(start, end);
  // eslint-disable-next-line no-new-func
  return new Function(`${fnSrc}; return formatAgeAgo;`)();
}

const formatAgeAgo = loadFormatter();
const S = 1000, M = 60 * S, H = 3600 * S, D = 86400 * S;

test('seconds: floor>=1s, never fake 0', () => {
  assert.equal(formatAgeAgo(0), '1s ago');
  assert.equal(formatAgeAgo(1 * S), '1s ago');
  assert.equal(formatAgeAgo(59 * S), '59s ago');
});

test('minutes show minutes+seconds, dropping a zero seconds unit', () => {
  assert.equal(formatAgeAgo(1 * M + 2 * S), '1m 2s ago');
  assert.equal(formatAgeAgo(6 * M + 30 * S), '6m 30s ago');
  assert.equal(formatAgeAgo(5 * M), '5m ago');
  assert.equal(formatAgeAgo(59 * M + 59 * S), '59m 59s ago');
});

test('hours show hours+minutes (capital H), dropping a zero minutes unit', () => {
  assert.equal(formatAgeAgo(1 * H + 2 * M), '1H 2m ago');
  assert.equal(formatAgeAgo(5 * H + 10 * M), '5H 10m ago');
  assert.equal(formatAgeAgo(2 * H), '2H ago');
  assert.equal(formatAgeAgo(2 * H + 45 * M + 59 * S), '2H 45m ago'); // seconds ignored at hour scale
});

test('days only (capital D), no smaller unit', () => {
  assert.equal(formatAgeAgo(1 * D), '1D ago');
  assert.equal(formatAgeAgo(2 * D + 5 * H + 30 * M), '2D ago');
});

test('garbage / negative input never fakes a fresh timer', () => {
  assert.equal(formatAgeAgo(-5), '1s ago');
  assert.equal(formatAgeAgo(NaN), '1s ago');
});
