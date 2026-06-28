'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { isPortalPriorityPath } = require('../src/portalPriorityPaths');

test('portal priority covers license and ad completion', () => {
  assert.ok(isPortalPriorityPath('/license', 'GET'));
  assert.ok(isPortalPriorityPath('/license/generate', 'POST'));
  assert.ok(isPortalPriorityPath('/unlock/linkvertise/complete', 'GET'));
  assert.ok(isPortalPriorityPath('/unlock/lootlabs/complete', 'GET'));
  assert.ok(isPortalPriorityPath('/key/result', 'GET'));
  assert.ok(isPortalPriorityPath('/api/license/eligibility', 'GET'));
  assert.ok(isPortalPriorityPath('/login', 'GET'));
});

test('portal priority does not steal tracker read API paths', () => {
  assert.equal(isPortalPriorityPath('/api/tracker/get-backpack/foo', 'GET'), false);
  assert.equal(isPortalPriorityPath('/api/fishit-tracker/get-backpack/foo', 'GET'), false);
  assert.equal(isPortalPriorityPath('/api/tracker/account-status', 'GET'), false);
  assert.equal(isPortalPriorityPath('/tracker', 'GET'), false);
});
