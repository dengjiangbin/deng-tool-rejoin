'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');

process.env.NODE_ENV = 'test';
process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://placeholder.supabase.co';
process.env.SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || 'test-service-role-key';
process.env.FISHIT_DB_PATH = process.env.FISHIT_DB_PATH || '/nonexistent/deng-fish-it.sqlite';

const stoneImageAssets = require('../src/fishitStoneImageAssets');
const totemImageAssets = require('../src/fishitTotemImageAssets');
const uploadStatus = require('../src/fishitTrackerUploadStatus');
const { deriveAccountPresenceStatus, ACCOUNT_PRESENCE_GRACE_MS } = require('../src/trackerAccountPresence');
const { buildPublicFishFields } = require('../src/fishitTrackerRoutes');
const gameItemDbPublic = require('../src/fishitGameItemDbPublic');

const BASE_URL = 'http://127.0.0.1:8791';

describe('status grace + stone/totem image URLs', () => {
  test('presence grace window is 10 minutes', () => {
    assert.equal(ACCOUNT_PRESENCE_GRACE_MS, 600_000);
    assert.equal(uploadStatus.PUBLIC_STATUS_GRACE_SECONDS, 600);
  });

  test('transient 502 keeps account green when last success is within grace', () => {
    const nowMs = Date.now();
    const session = {
      isOnline: true,
      lastSuccessfulUploadAt: new Date(nowMs - 120_000).toISOString(),
      lastAccountSeenAt: new Date(nowMs - 120_000).toISOString(),
      lastFailureReason: 'server_502_upload_retrying',
      lastUploadStatusCodeReturned: 502,
      lastStatus: 'green',
      intervalSeconds: 60,
    };
    const presence = deriveAccountPresenceStatus(session, ACCOUNT_PRESENCE_GRACE_MS, nowMs);
    assert.equal(presence.accountPresenceLive, true);
    const upload = uploadStatus.deriveTrackerUploadAccountStatus(session, { serverNowMs: nowMs });
    assert.equal(upload.statusColor, 'green');
  });

  test('all enchant stone types resolve tracker-read asset URLs', async () => {
    const stones = Object.entries(stoneImageAssets.ENCHANT_STONES).map(([id, meta]) => ({
      kind: 'stone',
      itemId: String(id),
      stoneType: meta.stoneType,
      name: meta.name,
      quantity: 1,
      icon: id === '246' ? 'rbxassetid://73883190545629' : 'rbxassetid://9999999999999',
      source: 'playerdata_gameitemdb',
      identityVerified: true,
    }));
    const pub = await buildPublicFishFields([], BASE_URL, {
      sessionData: {
        inventorySource: 'playerdata_gameitemdb',
        playerDataFishItems: [],
        playerDataStoneItems: stones,
        sourceTruth: gameItemDbPublic.defaultSourceTruth(),
      },
    });
    for (const stone of pub.stoneItems) {
      assert.ok(stone.imageUrlPresent, `missing image for ${stone.name}`);
      assert.match(stone.imageUrl, /^http:\/\/127\.0\.0\.1:8791\/api\/tracker\//);
      assert.doesNotMatch(stone.imageUrl, /stone_246_double/);
    }
    const transcended = pub.stoneItems.find((s) => s.itemId === '246');
    assert.equal(transcended.imageSource, 'stone_gameitemdb_proxy');
  });

  test('totem resolver rejects stale placeholder catalog and uses manual or proxy', () => {
    assert.ok(totemImageAssets.isStaleTotemCatalogFile('totem_mutation_totem.webp'));
    const mutation = totemImageAssets.attachTotemImagesToItems([{
      itemId: '2',
      name: 'Mutation Totem',
      quantity: 1,
      icon: 'rbxassetid://75593774049916',
      source: 'playerdata_gameitemdb',
    }], BASE_URL)[0];
    assert.match(mutation.imageUrl, /\/api\/tracker\/assets\/manual\/totems\//);

    const shiny = totemImageAssets.attachTotemImagesToItems([{
      itemId: '502',
      name: 'Shiny Totem',
      quantity: 1,
      icon: 'rbxassetid://9876543210987',
      source: 'playerdata_gameitemdb',
    }], BASE_URL)[0];
    // 2026-06-15: Shiny Totem now ships an explicit manual override image,
    // which must win over the gameDB icon proxy.
    assert.match(shiny.imageUrl, /\/api\/tracker\/assets\/manual\/totems\/shiny_totem_2026_06_15\.png/);
    assert.equal(shiny.imageSource, 'manual_override');
  });
});
