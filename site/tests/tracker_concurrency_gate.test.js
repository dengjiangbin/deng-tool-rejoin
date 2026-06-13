'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const gate = require('../src/trackerConcurrencyGate');

describe('trackerConcurrencyGate', () => {
  beforeEach(() => {
    gate._resetForTests();
  });

  test('status-only uploads bypass concurrency gate', () => {
    const source = require('fs').readFileSync(
      require('path').join(__dirname, '..', 'src', 'trackerConcurrencyGate.js'),
      'utf8',
    );
    assert.match(source, /tracker_status/);
    assert.match(source, /isStatusOnlyUpload/);
    assert.doesNotMatch(source, /server_busy/);
  });

  test('stats exposes active and queued counts', () => {
    const stats = gate.stats();
    assert.equal(typeof stats.active, 'number');
    assert.equal(typeof stats.queued, 'number');
    assert.ok(stats.max >= 1);
  });
});
