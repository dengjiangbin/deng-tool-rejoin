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

  test('sanitisePlayerStats auto-generates coinsText and totalCaughtText from numeric values', () => {
    const out = playerStats.sanitisePlayerStats({
      coins: 201200,
      totalCaught: 3077845,
      rarestFishChance: '1/25M',
    });
    assert.equal(out.coinsText, '201.2K');
    assert.equal(out.totalCaughtText, '3.077.845');
  });

  test('mergePlayerStats keeps trusted existing stats when incoming payload is empty', () => {
    const existing = playerStats.sanitisePlayerStats({
      coins: 201200,
      totalCaught: 450,
      rarestFishChance: '1/4.50K',
      source: 'leaderstats',
      build: 'BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10',
    });
    const merged = playerStats.mergePlayerStats(existing, null);
    assert.equal(merged.coinsText, '201.2K');
    assert.equal(merged.totalCaught, 450);
  });

  test('mergePlayerStats clears stale stats on live roblox missing payload', () => {
    const existing = playerStats.sanitisePlayerStats({
      coins: 201200,
      coinsText: '201.2K',
      totalCaught: 450,
      rarestFishChance: '1/4.50K',
      source: 'leaderstats',
    });
    const merged = playerStats.mergePlayerStats(existing, {
      source: 'missing',
      observedAt: 1710000001,
      build: 'BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10',
    }, { isLiveRoblox: true });
    assert.equal(merged.source, 'missing');
    assert.equal(merged.coinsText, undefined);
    assert.equal(merged.rarestFishChance, undefined);
  });

  test('mergePlayerStats keeps trusted existing stats when non-live incoming source is missing', () => {
    const existing = playerStats.sanitisePlayerStats({
      coins: 653200000,
      coinsText: '653.2M',
      totalCaught: 3077845,
      totalCaughtText: '3,077,845',
      rarestFishChance: '1/25M',
      source: 'replion',
      build: 'BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10',
    });
    const merged = playerStats.mergePlayerStats(existing, {
      source: 'missing',
      observedAt: 1710000000,
      build: 'BLOCKER10ZV_PLAYERSTATS_REPLION_LEADERSTATS_2026_06_10',
    });
    assert.equal(merged.coinsText, '653.2M');
    assert.equal(merged.rarestFishChance, '1/25M');
    assert.equal(merged.source, 'replion');
  });

  test('sanitisePlayerStatsDebug keeps coinProbe proof fields', () => {
    const out = playerStats.sanitisePlayerStatsDebug({
      enabled: true,
      source: 'replion',
      build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
      coinProbe: {
        source: 'replion',
        matchedPath: 'Coins',
        matchedKey: 'Coins',
        rawValue: '33.44M',
        parsedValue: 33440000,
        candidateKeys: ['Coins', 'Currencies.Coins'],
      },
    });
    assert.equal(out.coinProbe.source, 'replion');
    assert.equal(out.coinProbe.matchedPath, 'Coins');
    assert.equal(out.coinProbe.rawValue, '33.44M');
    assert.equal(out.coinProbe.parsedValue, 33440000);
    assert.deepEqual(out.coinProbe.candidateKeys, ['Coins', 'Currencies.Coins']);
    const withLeader = playerStats.sanitisePlayerStatsDebug({
      enabled: true,
      build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
      coinProbe: {
        source: 'missing',
        leaderstatsChildren: [{ name: 'Caught', value: '68,885' }],
      },
    });
    assert.deepEqual(withLeader.coinProbe.leaderstatsChildren, [{ name: 'Caught', value: '68,885' }]);
  });

  test('mergePlayerStats regenerates totalCaughtText when numeric totalCaught updates', () => {
    const existing = playerStats.sanitisePlayerStats({
      totalCaught: 58810,
      totalCaughtText: '58.810',
      source: 'leaderstats',
      build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
    });
    const merged = playerStats.mergePlayerStats(existing, {
      totalCaught: 58811,
      source: 'leaderstats',
      build: 'BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10',
    }, { isLiveRoblox: true });
    assert.equal(merged.totalCaught, 58811);
    assert.equal(merged.totalCaughtText, '58.811');
    assert.equal(playerStats.displayTotalCaught(merged), '58.811');
  });

  test('missing coin does not erase totalCaught and rarestFish on merge', () => {
    const existing = playerStats.sanitisePlayerStats({
      totalCaughtText: '54,313',
      rarestFishChance: '1/4M',
      source: 'leaderstats',
      build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
    });
    const merged = playerStats.mergePlayerStats(existing, {
      source: 'leaderstats',
      observedAt: 1710000002,
      build: 'BLOCKER10ZW_COINS_REPLION_PATH_PROBE_2026_06_10',
    }, { isLiveRoblox: true });
    assert.equal(merged.totalCaughtText, '54.313');
    assert.equal(merged.rarestFishChance, '1/4M');
    assert.equal(merged.coinsText, undefined);
  });

  test('mergePlayerStats rejects untrusted BLOCKER10ZV incoming stats', () => {
    const merged = playerStats.mergePlayerStats(null, {
      coinsText: '201.2K',
      totalCaught: 450,
      rarestFishChance: '1/4.50K',
      source: 'leaderstats',
      build: 'BLOCKER10ZV_PLAYERSTATS_REPLION_LEADERSTATS_2026_06_10',
    }, { isLiveRoblox: true });
    assert.equal(merged, null);
  });

  test('displayablePlayerStats rejects stale BLOCKER10ZV seeded stats', () => {
    const stale = playerStats.sanitisePlayerStats({
      coins: 201200,
      coinsText: '201.2K',
      totalCaught: 450,
      rarestFishChance: '1/4.50K',
      source: 'leaderstats',
      build: 'BLOCKER10ZV_PLAYERSTATS_REPLION_LEADERSTATS_2026_06_10',
    });
    assert.equal(playerStats.displayablePlayerStats(stale), null);
    assert.equal(playerStats.displayCoins(stale), '—');
    assert.equal(playerStats.displayTotalCaught(stale), '—');
    assert.equal(playerStats.displayRarestFish(stale), '—');
  });

  test('displayablePlayerStats accepts BLOCKER10ZW missing probe as empty display', () => {
    const missing = {
      source: 'missing',
      build: 'BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10',
      observedAt: 1710000001,
    };
    const out = playerStats.displayablePlayerStats(missing);
    assert.equal(out.source, 'missing');
    assert.equal(playerStats.displayCoins(out), '—');
    assert.equal(playerStats.displayRarestFish(out), '—');
  });

  test('displayablePlayerStats accepts BLOCKER10ZW replion values', () => {
    const real = playerStats.sanitisePlayerStats({
      coinsText: '33.44M',
      totalCaughtText: '68,885',
      rarestFishChance: '1/1.20M',
      source: 'replion',
      build: 'BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10',
    });
    assert.equal(playerStats.displayCoins(real), '33.44M');
    assert.equal(playerStats.displayTotalCaught(real), '68.885');
    assert.equal(playerStats.displayRarestFish(real), '1/1.20M');
  });

  test('display helpers format compact values and progress', () => {
    const stats = playerStats.sanitisePlayerStats({
      coins: 201200,
      totalCaught: 450,
      rarestFishChance: '1/4.50K',
      ruin: { current: 4, max: 4 },
      source: 'leaderstats',
      build: 'BLOCKER10ZW_PLAYERSTATS_REAL_ONLY_2026_06_10',
    });
    assert.equal(playerStats.displayCoins(stats), '201.2K');
    assert.equal(playerStats.displayTotalCaught(stats), '450');
    assert.equal(playerStats.displayRarestFish(stats), '1/4.50K');
    assert.equal(playerStats.displayProgress(stats, 'ruin'), '4/4');
    assert.equal(playerStats.isProgressComplete(stats, 'ruin'), true);
  });
});
