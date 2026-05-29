'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const { formatExact } = require('../src/formatNumbers');

describe('formatExact', () => {
  test('2095 renders 2,095 not 2.1K', () => {
    assert.equal(formatExact(2095), '2,095');
  });

  test('54203 renders 54,203 not 54.2K', () => {
    assert.equal(formatExact(54203), '54,203');
  });

  test('zero and invalid inputs', () => {
    assert.equal(formatExact(0), '0');
    assert.equal(formatExact(null), '0');
    assert.equal(formatExact('nope'), '0');
  });
});
