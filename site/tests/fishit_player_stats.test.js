'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const playerStats = require('../src/fishitPlayerStats');

describe('fishitPlayerStats', () => {
  test('sanitisePlayerStats keeps coins, caught, rarest fish, ruin, artifact only', () => {
    const out = playerStats.sanitisePlayerStats({
      coins: 653200000,
      coinsText: '653.2M',
      totalCaught: 3077845,
      rarestFishChance: '1/25M',
      ruin: { current: 4, max: 4 },
      artifact: '0/4',
      quest: { current: 1, max: 4 },
      elementFlags: ['x'],
    });
    assert.equal(out.coins, 653200000);
    assert.equal(out.coinsText, '653.2M');
    assert.equal(out.totalCaught, 3077845);
    assert.equal(out.rarestFishChance, '1/25M');
    assert.deepEqual(out.ruin, { current: 4, max: 4 });
    assert.deepEqual(out.artifact, { current: 0, max: 4 });
    assert.equal(out.quest, undefined);
    assert.equal(out.elementFlags, undefined);
  });

  test('display helpers format compact values and progress', () => {
    const stats = playerStats.sanitisePlayerStats({
      coins: 201200,
      totalCaught: 450,
      rarestFishChance: '1/4.50K',
      ruin: { current: 4, max: 4 },
    });
    assert.equal(playerStats.displayCoins(stats), '201.2K');
    assert.equal(playerStats.displayTotalCaught(stats), '450');
    assert.equal(playerStats.displayRarestFish(stats), '1/4.50K');
    assert.equal(playerStats.displayProgress(stats, 'ruin'), '4/4');
    assert.equal(playerStats.isProgressComplete(stats, 'ruin'), true);
  });
});
