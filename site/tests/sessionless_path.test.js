'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const { isSessionlessPath } = require('../src/publicDomain');

describe('sessionless path policy', () => {
  test('upload POST paths skip session', () => {
    assert.equal(isSessionlessPath('/api/fishit-tracker/update-backpack', 'POST'), true);
    assert.equal(isSessionlessPath('/api/tracker/update-backpack', 'POST'), true);
  });

  test('tracker read/auth GET paths load session', () => {
    assert.equal(isSessionlessPath('/api/tracker/summary', 'GET'), false);
    assert.equal(isSessionlessPath('/api/tracker/account-summary', 'GET'), false);
    assert.equal(isSessionlessPath('/api/inventory/accounts', 'GET'), false);
    assert.equal(isSessionlessPath('/tracker', 'GET'), false);
    assert.equal(isSessionlessPath('/dashboard', 'GET'), false);
  });
});
