'use strict';

// Phase 9 regression: the server-side Ruby Gemstone counter must count fish
// INSTANCES whose name is "Ruby" carrying a Gemstone-family mutation — the real
// Fish It payload shape (card-level mutation null, per-instance mutation set).
// It must NOT require an item literally labelled "Ruby Gemstone".

const { test } = require('node:test');
const assert = require('node:assert');
const ruby = require('../src/fishitRubyGemstoneCount');

test('Phase 9: counts per-instance Gemstone mutations on a "Ruby" card (real payload shape)', () => {
  const body = {
    fishItems: [
      {
        name: 'Ruby',
        cleanName: 'Ruby',
        mutation: null,
        ownedInstances: [
          { mutationName: 'Gemstone', weightKg: 1.2 },
          { mutation: 'Gemstone', weightKg: 2.0 },
          { mutation: 'Gold' },
        ],
      },
      { name: 'Cerulean Dragon', cleanName: 'Cerulean Dragon', ownedInstances: [{ mutation: 'Shiny' }] },
    ],
  };
  const r = ruby.computeRubyGemstoneTopCard(body);
  assert.strictEqual(r.count, 2);
  assert.ok(r.matchedRows.length >= 1);
});

test('Phase 9: counts an aggregated card-level Gemstone row by amount', () => {
  const body = { fishItems: [{ name: 'Ruby', cleanName: 'Ruby', mutation: 'Gemstone', amount: 5 }] };
  assert.strictEqual(ruby.computeRubyGemstoneTopCard(body).count, 5);
});

test('Phase 9: does NOT count a plain Ruby fish with no gemstone mutation', () => {
  const body = { fishItems: [{ name: 'Ruby', cleanName: 'Ruby', ownedInstances: [{ mutation: 'Gold' }] }] };
  assert.strictEqual(ruby.computeRubyGemstoneTopCard(body).count, 0);
});

test('Phase 9: does NOT count a non-Ruby fish that has a Gemstone mutation', () => {
  const body = { fishItems: [{ name: 'Sapphire', cleanName: 'Sapphire', ownedInstances: [{ mutation: 'Gemstone' }] }] };
  assert.strictEqual(ruby.computeRubyGemstoneTopCard(body).count, 0);
});

test('Phase 9: counts gemstone mutation spelling aliases ("gem stone", "Ruby Gemstone")', () => {
  const body = { fishItems: [{ name: 'Ruby', cleanName: 'Ruby', ownedInstances: [{ mutation: 'gem stone' }, { mutation: 'Ruby Gemstone' }] }] };
  assert.strictEqual(ruby.computeRubyGemstoneTopCard(body).count, 2);
});
