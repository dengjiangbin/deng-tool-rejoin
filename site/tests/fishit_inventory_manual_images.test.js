'use strict';

const { describe, test, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');
const express = require('express');
const request = require('supertest');

process.env.NODE_ENV = 'test';
process.env.TOOL_SITE_ADMIN_TOKEN = 'test-admin-token-manual-images';

const manualImages = require('../src/fishitInventoryManualImages');
const gameItemDbPublic = () => require('../src/fishitGameItemDbPublic');
const adminRoutes = require('../src/fishitInventoryManualImageAdminRoutes');

const FIXTURE_DIR = path.join(__dirname, '..', 'data', 'manual_image_seed');
const MUTATION_PNG = path.join(FIXTURE_DIR, 'mutation_totem.png');
const LUCK_PNG = path.join(FIXTURE_DIR, 'luck_totem.png');

let tmpDir;
let origDataPath;
let origCacheDir;

function makeApp() {
  const app = express();
  app.set('view engine', 'ejs');
  app.set('views', path.join(__dirname, '..', 'views'));
  app.use(adminRoutes);
  return app;
}

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'manual-img-'));
  process.env.FISHIT_INVENTORY_MANUAL_IMAGES_PATH = path.join(tmpDir, 'catalog.json');
  process.env.FISHIT_MANUAL_IMAGE_CACHE_DIR = path.join(tmpDir, 'cache');
  fs.writeFileSync(process.env.FISHIT_INVENTORY_MANUAL_IMAGES_PATH, JSON.stringify({ version: 1, overrides: {} }), 'utf8');
  delete require.cache[require.resolve('../src/fishitInventoryManualImages')];
  delete require.cache[require.resolve('../src/fishitGameItemDbPublic')];
});

afterEach(() => {
  delete process.env.FISHIT_INVENTORY_MANUAL_IMAGES_PATH;
  delete process.env.FISHIT_MANUAL_IMAGE_CACHE_DIR;
  delete require.cache[require.resolve('../src/fishitInventoryManualImages')];
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

function manualModule() {
  return require('../src/fishitInventoryManualImages');
}

function readImageBase64(filePath) {
  const buf = fs.readFileSync(filePath);
  return `data:image/png;base64,${buf.toString('base64')}`;
}

describe('inventory manual image overrides', () => {
  test('Mutation Totem manual upload wins over failed auto resolver', async () => {
    assert.ok(fs.existsSync(MUTATION_PNG), 'mutation_totem fixture required');
    const manualImages = manualModule();
    const entry = manualImages.upsertManualOverride({
      category: 'totems',
      itemId: '2',
      name: 'Mutation Totem',
      imageBase64: readImageBase64(MUTATION_PNG),
      mimeType: 'image/png',
    });
    assert.equal(entry.category, 'totems');
    assert.equal(entry.itemId, '2');
    assert.match(entry.imageUrl, /^\/api\/fishit-tracker\/assets\/manual\/totems\//);

    const sessionData = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataTotemItems: [{
        kind: 'totem',
        itemId: '2',
        name: 'Mutation Totem',
        quantity: 60,
        icon: 'rbxassetid://75593774049916',
        source: 'playerdata_gameitemdb',
        identityVerified: true,
      }],
      sourceTruth: { globalDbUsedForPublicIdentity: false, identity: 'playerdata_itemutility_gameitemdb' },
    };
    const publicData = await gameItemDbPublic().buildPublicFromPlayerDataGameItemDb(sessionData, 'http://localhost', {
      fishImageCache: require('../src/fishitFishImageCache'),
    });
    const totem = (publicData.totemItems || []).find((t) => String(t.itemId) === '2');
    assert.ok(totem, 'Mutation Totem row expected');
    assert.equal(totem.name, 'Mutation Totem');
    assert.equal(totem.imageResolved, true);
    assert.equal(totem.imageSource, manualModule().MANUAL_OVERRIDE_SOURCE);
    assert.match(totem.imageUrl, /^http:\/\/localhost\/api\/fishit-tracker\/assets\/manual\/totems\//);
  });

  test('manual image persists after poll refresh rebuild', async () => {
    assert.ok(fs.existsSync(LUCK_PNG), 'luck_totem fixture required');
    const manualImages = manualModule();
    manualImages.upsertManualOverride({
      category: 'totems',
      itemId: '3',
      name: 'Luck Totem',
      imageBase64: readImageBase64(LUCK_PNG),
      mimeType: 'image/png',
    });
    const sessionData = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataTotemItems: [{ kind: 'totem', itemId: '3', name: 'Luck Totem', quantity: 5, source: 'playerdata_gameitemdb' }],
      sourceTruth: { globalDbUsedForPublicIdentity: false, identity: 'playerdata_itemutility_gameitemdb' },
    };
    const first = await gameItemDbPublic().buildPublicFromPlayerDataGameItemDb(sessionData, 'http://localhost', {
      fishImageCache: require('../src/fishitFishImageCache'),
    });
    const second = await gameItemDbPublic().buildPublicFromPlayerDataGameItemDb(sessionData, 'http://localhost', {
      fishImageCache: require('../src/fishitFishImageCache'),
    });
    assert.equal(first.totemItems[0].imageSource, manualModule().MANUAL_OVERRIDE_SOURCE);
    assert.equal(second.totemItems[0].imageUrl, first.totemItems[0].imageUrl);
    assert.equal(second.totemItems[0].imageResolved, true);
  });

  test('manual image survives catalog reload (PM2 restart simulation)', () => {
    assert.ok(fs.existsSync(MUTATION_PNG), 'mutation_totem fixture required');
    const manualImages = manualModule();
    manualImages.upsertManualOverride({
      category: 'totems',
      itemId: '2',
      name: 'Mutation Totem',
      imageBase64: readImageBase64(MUTATION_PNG),
      mimeType: 'image/png',
    });
    manualImages._resetCatalogForTests();
    const reloaded = manualImages.lookupManualOverride({ itemId: '2', name: 'Mutation Totem' }, 'totems');
    assert.ok(reloaded);
    assert.match(reloaded.imageUrl, /^\/api\/fishit-tracker\/assets\/manual\/totems\//);
    assert.ok(manualImages.manualFileExists('totems', reloaded.uploadedFile));
  });

  test('other totems can be manually mapped without code changes', async () => {
    assert.ok(fs.existsSync(LUCK_PNG), 'luck_totem fixture required');
    const manualImages = manualModule();
    manualImages.upsertManualOverride({
      category: 'totems',
      itemId: '999',
      name: 'Custom Event Totem',
      imageBase64: readImageBase64(LUCK_PNG),
      mimeType: 'image/png',
    });
    const sessionData = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataTotemItems: [{ kind: 'totem', itemId: '999', name: 'Custom Event Totem', quantity: 1, source: 'playerdata_gameitemdb' }],
      sourceTruth: { globalDbUsedForPublicIdentity: false, identity: 'playerdata_itemutility_gameitemdb' },
    };
    const publicData = await gameItemDbPublic().buildPublicFromPlayerDataGameItemDb(sessionData, 'http://localhost', {
      fishImageCache: require('../src/fishitFishImageCache'),
    });
    assert.equal(publicData.totemItems[0].imageSource, manualModule().MANUAL_OVERRIDE_SOURCE);
    assert.equal(publicData.totemItems[0].imageResolved, true);
  });

  test('auto resolver still works when no manual override exists', async () => {
    const sessionData = {
      inventorySource: 'playerdata_gameitemdb',
      playerDataTotemItems: [{
        kind: 'totem',
        itemId: '502',
        name: 'Shiny Totem',
        quantity: 1,
        icon: 'rbxassetid://12345678901',
        source: 'playerdata_gameitemdb',
      }],
      sourceTruth: { globalDbUsedForPublicIdentity: false, identity: 'playerdata_itemutility_gameitemdb' },
    };
    const publicData = await gameItemDbPublic().buildPublicFromPlayerDataGameItemDb(sessionData, 'http://localhost', {
      fishImageCache: require('../src/fishitFishImageCache'),
    });
    const totem = publicData.totemItems[0];
    assert.notEqual(totem.imageSource, manualModule().MANUAL_OVERRIDE_SOURCE);
  });

  test('refreshManualImagesOnPublicItems upgrades stale auto totem cards', () => {
    assert.ok(fs.existsSync(MUTATION_PNG), 'mutation_totem fixture required');
    const manualImages = manualModule();
    manualImages.upsertManualOverride({
      category: 'totems',
      itemId: '2',
      name: 'Mutation Totem',
      imageBase64: readImageBase64(MUTATION_PNG),
      mimeType: 'image/png',
    });
    const stale = [{
      kind: 'totem',
      category: 'totem',
      itemId: '501',
      name: 'Mutation Totem',
      amount: 3,
      quantity: 3,
      imageUrl: '/api/fishit-tracker/assets/totems/totem_mutation_totem.webp',
      imageSource: 'totem_manual_asset',
      imageResolved: true,
    }];
    const refreshed = manualImages.refreshManualImagesOnPublicItems(stale, 'totems', 'https://tool.deng.my.id');
    assert.equal(refreshed[0].imageSource, manualImages.MANUAL_OVERRIDE_SOURCE);
    assert.match(refreshed[0].imageUrl, /\/api\/fishit-tracker\/assets\/manual\/totems\/eef3af93/);
  });

  test('upload endpoint stores manual override', async () => {
    assert.ok(fs.existsSync(MUTATION_PNG), 'mutation_totem fixture required');
    const app = makeApp();
    const res = await request(app)
      .post('/api/fishit-tracker/admin/inventory-image-upload')
      .set('x-admin-token', 'test-admin-token-manual-images')
      .send({
        category: 'totems',
        itemId: '2',
        name: 'Mutation Totem',
        imageBase64: readImageBase64(MUTATION_PNG),
        mimeType: 'image/png',
      })
      .expect(200);
    assert.equal(res.body.ok, true);
    assert.equal(res.body.entry.imageSource, manualModule().MANUAL_OVERRIDE_SOURCE);
    assert.match(res.body.entry.imageUrl, /\/api\/fishit-tracker\/assets\/manual\/totems\//);
  });
});
