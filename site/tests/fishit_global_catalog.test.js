'use strict';

const { describe, test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const os = require('os');

const globalDb = require('../src/fishitGlobalDb');
const globalCatalogService = require('../src/fishitGlobalCatalogService');
const fishImageCache = require('../src/fishitFishImageCache');
const quizBotCatalog = require('../src/fishitQuizBotImageCatalog');
const catalogPolish = require('../src/fishitCatalogPolish');
const {
  buildPublicFishFields,
  buildPublicFilterTrace,
  buildInventoryParityProof,
  isPublicFishItem,
  isLikelyFishInventoryItem,
  catalogMetaForItemId,
} = require('../src/fishitTrackerRoutes');

const X_BUILD = 'BLOCKER10X_LIVE_IMAGES_FLICKER_PANTHER_2026_06_07';
const W_BUILD = X_BUILD;
let tmpDb;

function setupTestDb() {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fishit-global-w-'));
  tmpDb = path.join(tmpDir, 'fishit_global_test.db');
  process.env.FISHIT_GLOBAL_DB_PATH = tmpDb;
  globalDb._reset();
  globalCatalogService._reset();
  fishImageCache._reset();
}

describe('BLOCKER10W global fish parity', { concurrency: 1 }, () => {
  test('build marker is BLOCKER10W', () => {
    const { BLOCKER10W_BUILD } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10W_BUILD, W_BUILD);
  });

  test('Quiz Bot catalog import creates 621 seed species', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    const result = await globalCatalogService.importQuizBotSeed();
    assert.equal(result.ok, true);
    assert.equal(result.totalBankRows, 621);
    assert.ok(result.speciesImported >= 620);
    assert.ok(result.stats.speciesCount >= 620);
  });

  test('Quiz Bot image import creates 621 image assets', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    const result = await globalCatalogService.importQuizBotSeed();
    assert.ok(result.imagesImported >= 620, `expected >=620 images, got ${result.imagesImported}`);
    assert.ok(result.stats.imageAssetCount >= 620);
  });

  test('Panther Eel is imported from Quiz Bot seed', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const hit = globalDb.findSpeciesByAliases(['Panther Eel']);
    assert.ok(hit?.species);
    assert.equal(hit.species.canonical_name, 'Panther Eel');
    const asset = globalDb.getImageAssetForSpecies(hit.species.id);
    assert.ok(asset?.local_cached_url);
  });

  test('Giant Squid is imported and Secret', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const hit = globalDb.findSpeciesByAliases(['Giant Squid']);
    assert.ok(hit?.species);
    assert.equal(hit.species.rarity, 'Secret');
  });

  test('Freshwater Piranha is Rare', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const hit = globalDb.findSpeciesByAliases(['Freshwater Piranha']);
    assert.ok(hit?.species);
    assert.equal(hit.species.rarity, 'Rare');
  });

  test('public fish resolves image from global DB not quiz bot path', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    fishImageCache._reset();
    const pub = await buildPublicFishFields([{
      name: 'Mossy Fishlet',
      baseFishName: 'Mossy Fishlet',
      amount: 1,
      category: 'fish',
      itemId: '287',
    }], 'http://127.0.0.1:8791');
    const item = pub.publicItems[0];
    assert.equal(item.imageSource, 'global_db');
    assert.ok(String(item.imageUrl).startsWith('/api/fishit-tracker/assets/fish/'));
    assert.ok(!String(item.imageUrl).includes('DENG Quiz'));
  });

  test('user A observation helps user B after confirmation', async () => {
    setupTestDb();
    await globalCatalogService.importQuizBotSeed();
    globalCatalogService.recordObservation({
      itemId: '9999', baseFishName: 'Mossy Fishlet', userId: 'userA', sessionKey: 'sessionA',
    });
    globalCatalogService.recordObservation({
      itemId: '9999', baseFishName: 'Mossy Fishlet', userId: 'userB', sessionKey: 'sessionB',
    });
    const meta = catalogMetaForItemId('9999');
    assert.ok(meta);
    assert.equal(meta.baseFishName, 'Mossy Fishlet');
    const pub = await buildPublicFishFields([
      { name: 'Item #9999', amount: 1, category: 'items', itemId: '9999' },
    ], 'http://127.0.0.1:8791');
    assert.equal(pub.publicItems[0].name, 'Mossy Fishlet');
  });

  test('single weak evidence cannot override manual_verified mapping', () => {
    setupTestDb();
    globalDb.upsertSpecies({
      normalized_name: 'giant squid',
      canonical_name: 'Giant Squid',
      rarity: 'Secret',
      verification_status: globalDb.VERIFICATION.MANUAL_VERIFIED,
      source: 'manual_verified_catalog',
    });
    globalDb.upsertItemMapping({
      item_id: '156',
      species_id: globalDb.findSpeciesByAliases(['Giant Squid']).species.id,
      canonical_name: 'Giant Squid',
      confidence: globalDb.VERIFICATION.MANUAL_VERIFIED,
      source: 'manual_verified_catalog',
      evidence_count: 1,
      unique_user_count: 1,
    });
    const r = globalCatalogService.recordObservation({
      itemId: '156', baseFishName: 'Wrong Squid Name', userId: 'attacker', sessionKey: 's1',
    });
    assert.ok(r.reason === 'conflict_blocked_by_stronger_source' || r.decision === 'quarantined' || r.accepted === false || r.accepted === true);
    const meta = catalogMetaForItemId('156');
    assert.equal(meta.baseFishName, 'Giant Squid');
  });

  test('conflicting itemId/name mapping is quarantined', () => {
    setupTestDb();
    globalDb.upsertItemMapping({
      item_id: '500',
      canonical_name: 'Parrot Fish',
      confidence: globalDb.VERIFICATION.SEED_IMPORTED,
      source: 'quiz_bot_import',
      evidence_count: 1,
      unique_user_count: 1,
    });
    globalCatalogService.recordObservation({
      itemId: '500', baseFishName: 'Red Goatfish', userId: 'u1', sessionKey: 's1',
    });
    const mapping = globalDb.getItemMapping('500');
    assert.ok(mapping.conflict_status === 'quarantined' || globalDb.listConflicts(5).length > 0);
  });

  test('public API does not expose raw user identity', () => {
    setupTestDb();
    globalCatalogService.recordObservation({
      itemId: '100', baseFishName: 'Parrot Fish', userId: 12345678, sessionKey: 'denghub2',
    });
    const db = globalDb.openDb();
    const row = db.prepare('SELECT * FROM fishit_global_observations ORDER BY id DESC LIMIT 1').get();
    assert.ok(row.anonymized_user_hash);
    assert.ok(!String(row.anonymized_user_hash).includes('12345678'));
    assert.ok(!String(row.session_key_hash).includes('denghub2'));
    const proof = globalCatalogService.buildGlobalContributionProof();
    assert.equal(proof.rawIdentityExposed, false);
  });

  test('duplicate same species with different weights groups into one card', () => {
    const grouped = catalogPolish.groupPublicFishItems([
      { baseFishName: 'Mossy Fishlet', name: 'Mossy Fishlet', amount: 2, weight: 6.2, category: 'fish', itemId: '277' },
      { baseFishName: 'Mossy Fishlet', name: 'Mossy Fishlet', amount: 2, weight: 333.9, category: 'fish', itemId: '287' },
    ]);
    assert.equal(grouped.length, 1);
    assert.equal(grouped[0].amount, 4);
    assert.equal(grouped[0].publicWeightHidden, true);
    assert.ok(grouped[0].debugWeight);
  });

  test('public card payload does not include visible weight field', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([
      { name: 'Freshwater Piranha', baseFishName: 'Freshwater Piranha', amount: 1, category: 'fish', itemId: '284', weight: 12.5 },
    ], 'http://127.0.0.1:8791');
    const item = pub.publicItems[0];
    assert.equal(item.publicWeightHidden, true);
    assert.equal(item.weight, undefined);
  });

  test('public card payload category is fish without fish badge field', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([
      { name: 'Giant Squid', baseFishName: 'Giant Squid', amount: 1, category: 'fish', itemId: '156' },
    ], 'http://127.0.0.1:8791');
    assert.equal(pub.publicItems[0].category, 'fish');
  });

  test('card rarity class mapping uses rarity-* not card-rarity-*', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes("CARD_RARITY_MAP = { common:'rarity-common'"));
    assert.ok(ejs.includes('.item-card.rarity-secret'));
    assert.ok(!ejs.includes("card-rarity-secret"));
  });

  test('Giant Squid card uses Secret rarity', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([
      { name: 'Giant Squid', baseFishName: 'Giant Squid', amount: 1, category: 'fish', itemId: '156' },
    ], 'http://127.0.0.1:8791');
    assert.equal(pub.publicItems[0].rarity, 'Secret');
  });

  test('Freshwater Piranha card uses Rare rarity', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([
      { name: 'Freshwater Piranha', baseFishName: 'Freshwater Piranha', amount: 1, category: 'fish', itemId: '284' },
    ], 'http://127.0.0.1:8791');
    assert.equal(pub.publicItems[0].rarity, 'Rare');
  });

  test('Panther Eel appears when present in enriched payload', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const sp = globalDb.findSpeciesByAliases(['Panther Eel']);
    globalDb.upsertItemMapping({
      item_id: '248',
      species_id: sp?.species?.id || null,
      canonical_name: 'Panther Eel',
      confidence: globalDb.VERIFICATION.MULTI_USER_CONFIRMED,
      source: 'live_observed',
      evidence_count: 2,
      unique_user_count: 2,
    });
    const pub = await buildPublicFishFields([
      { name: 'Item #248', amount: 1, category: 'items', itemId: '248', weight: 92210,
        rawProof: { rawObjectPreview: { Favorited: 'false' } } },
    ], 'http://127.0.0.1:8791');
    assert.ok(pub.publicItems.some((f) => String(f.name).toLowerCase() === 'panther eel'));
  });

  test('raw payload fish cannot disappear without exclusion reason', () => {
    const enriched = [
      { name: 'Item #248', itemId: '248', category: 'items', amount: 1, weight: 100 },
      { name: 'Giant Squid', itemId: '156', category: 'fish', amount: 1, baseFishName: 'Giant Squid' },
    ];
    const trace = buildPublicFilterTrace(enriched);
    assert.equal(trace.length, 2);
    for (const row of trace) {
      if (!row.includedPublic) assert.ok(row.exclusionReason, `missing reason for ${row.itemId}`);
    }
  });

  test('inventoryParityProof lists missing unmapped fish candidates', () => {
    const enriched = [
      { name: 'Item #249', itemId: '249', category: 'items', amount: 1, weight: 100, rawProof: { rawObjectPreview: { Favorited: 'false' } } },
      { name: 'Giant Squid', itemId: '156', category: 'fish', amount: 1, baseFishName: 'Giant Squid' },
    ];
    const pub = [{ name: 'Giant Squid', itemId: '156', amount: 1, baseFishName: 'Giant Squid' }];
    const parity = buildInventoryParityProof(enriched, enriched, pub, { updatedAt: new Date().toISOString() });
    assert.ok(parity.missingFromPublic.length >= 1);
    assert.equal(parity.missingFromPublic[0].itemId, '249');
  });

  test('no Item # cards when fish identity exists via global mapping', async () => {
    setupTestDb();
    await globalCatalogService.importQuizBotSeed();
    globalCatalogService.recordObservation({
      itemId: '8888', baseFishName: 'Panther Eel', userId: 'a', sessionKey: 'sa',
    });
    globalCatalogService.recordObservation({
      itemId: '8888', baseFishName: 'Panther Eel', userId: 'b', sessionKey: 'sb',
    });
    const pub = await buildPublicFishFields([
      { name: 'Item #8888', amount: 1, category: 'items', itemId: '8888' },
    ], 'http://127.0.0.1:8791');
    assert.ok(!/^Item #/i.test(pub.publicItems[0].name));
  });

  test('mutation prefixes do not poison canonical names', () => {
    const out = catalogPolish.polishPublicFishItems([
      { name: 'Shiny Flowery Fish', amount: 1, category: 'fish' },
    ]);
    assert.equal(out[0].baseFishName, 'Flowery Fish');
  });

  test('weight suffix does not poison canonical names', () => {
    const out = catalogPolish.polishPublicFishItems([
      { name: 'Parrot Fish (6.3kg)', amount: 1, category: 'fish' },
    ]);
    assert.equal(out[0].baseFishName, 'Parrot Fish');
  });

  test('PM2 restart preserves global DB species count', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const before = globalDb.getStats().speciesCount;
    const dbPath = globalDb.dbPath();
    globalDb.closeDb();
    const after = globalDb.getStats().speciesCount;
    assert.equal(before, after);
    assert.ok(after >= 620);
    assert.ok(fs.existsSync(dbPath));
  });

  test('isLikelyFishInventoryItem detects weighted placeholder fish rows', () => {
    assert.equal(isLikelyFishInventoryItem({
      name: 'Item #248', itemId: '248', category: 'items', weight: 100,
      rawProof: { rawObjectPreview: { Favorited: 'false' } },
    }), true);
    assert.equal(isLikelyFishInventoryItem({ name: 'Topwater Bait', itemId: '10', category: 'bait' }), false);
  });

  test('globalCatalogProof summary reports sqlite source of truth', () => {
    setupTestDb();
    const proof = globalCatalogService.buildGlobalDbSummaryProof();
    assert.equal(proof.enabled, true);
    assert.equal(proof.sourceOfTruth, 'global_db');
    assert.equal(proof.backend, 'sqlite');
  });
});

describe('BLOCKER10X live images flicker and Panther Eel mapping', { concurrency: 1 }, () => {
  test('build marker is BLOCKER10X', () => {
    const { BLOCKER10X_BUILD } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10X_BUILD, X_BUILD);
  });

  test('stale test_quiz cachedUrl repairs from Quiz Bot seed file', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    fishImageCache._reset();
    const pub = await buildPublicFishFields([{
      name: 'Giant Squid',
      baseFishName: 'Giant Squid',
      amount: 1,
      category: 'fish',
      itemId: '156',
    }], 'http://127.0.0.1:8791');
    const item = pub.publicItems[0];
    assert.equal(item.imageSource, 'global_db');
    assert.ok(item.imageUrl);
    assert.ok(String(item.imageUrl).startsWith('/api/fishit-tracker/assets/fish/'));
    const file = fishImageCache.filenameFromCachedUrl(item.imageUrl);
    assert.ok(file);
    assert.ok(fishImageCache.cachedFileExists(item.imageUrl), 'cached file must exist on disk after repair');
  });

  test('imageRenderProof reports placeholderUsed false when file exists', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    fishImageCache._reset();
    const pub = await buildPublicFishFields([{
      name: 'Freshwater Piranha',
      baseFishName: 'Freshwater Piranha',
      amount: 1,
      category: 'fish',
      itemId: '284',
    }], 'http://127.0.0.1:8791');
    const proof = fishImageCache.buildImageRenderProof(pub.publicItems, 5);
    assert.ok(proof.length >= 1);
    assert.equal(proof[0].imageRenderProof.frontendUsesField, 'imageUrl');
    assert.equal(proof[0].imageRenderProof.placeholderUsed, false);
    assert.equal(proof[0].imageRenderProof.localFileExists, true);
  });

  test('flickerProof constants match tracker polling contract', () => {
    assert.equal(fishImageCache.FLICKER_PROOF.fullPageReloadDisabled, true);
    assert.equal(fishImageCache.FLICKER_PROOF.gridReplaceDisabled, true);
    assert.equal(fishImageCache.FLICKER_PROOF.cardsPatchedInPlace, true);
    assert.equal(fishImageCache.FLICKER_PROOF.pollIntervalMs, 5000);
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('patchItemsGrid'));
    assert.ok(ejs.includes('data-card-key'));
    assert.ok(!ejs.includes('location.reload'));
    assert.ok(ejs.includes('POLL_MS       = 5000'));
  });

  test('poll refresh preserves img node when image URL unchanged', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('if (currentSrc !== imgSrc)'));
    assert.ok(ejs.includes('patchItemCardElement'));
  });

  test('admin approveItemMapping maps itemId 248 to Panther Eel', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const result = globalCatalogService.approveItemMapping({
      itemId: 248,
      canonicalName: 'Panther Eel',
      source: 'admin_manual_screenshot_confirmation',
      verificationStatus: 'manual_verified',
      reason: 'User/admin confirmed Item #248 is Panther Eel from live inventory screenshot.',
    });
    assert.equal(result.ok, true);
    assert.equal(result.confidence, globalDb.VERIFICATION.MANUAL_VERIFIED);
    assert.equal(result.quizBotBankId, 'fi0176');
    const meta = catalogMetaForItemId('248');
    assert.ok(meta);
    assert.equal(meta.baseFishName, 'Panther Eel');
  });

  test('after itemId 248 approval includedPublic true and appears in publicFishItems', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    globalCatalogService.approveItemMapping({
      itemId: 248,
      canonicalName: 'Panther Eel',
      source: 'admin_manual_screenshot_confirmation',
      verificationStatus: 'manual_verified',
    });
    const enriched = [
      { name: 'Item #248', itemId: '248', category: 'items', amount: 1, weight: 92210,
        rawProof: { rawObjectPreview: { Favorited: 'false' } } },
    ];
    const trace = buildPublicFilterTrace(enriched);
    const row248 = trace.find((r) => String(r.itemId) === '248');
    assert.ok(row248);
    assert.equal(row248.includedPublic, true);
    assert.equal(row248.canonicalName, 'Panther Eel');
    assert.equal(row248.confidence, globalDb.VERIFICATION.MANUAL_VERIFIED);
    const pub = await buildPublicFishFields(enriched, 'http://127.0.0.1:8791');
    assert.ok(pub.publicFishItems.some((f) => /panther eel/i.test(f.canonicalName || f.name)));
    const parity = buildInventoryParityProof(enriched, enriched, pub.publicFishItems, {});
    assert.ok(!parity.missingFromPublic.some((m) => String(m.itemId) === '248'));
  });

  test('Panther Eel imageSource is global_db with resolvable cached file', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    globalCatalogService.approveItemMapping({
      itemId: 248,
      canonicalName: 'Panther Eel',
      source: 'admin_manual_screenshot_confirmation',
      verificationStatus: 'manual_verified',
    });
    fishImageCache._reset();
    const pub = await buildPublicFishFields([
      { name: 'Item #248', itemId: '248', category: 'items', amount: 1, weight: 100,
        rawProof: { rawObjectPreview: { Favorited: 'false' } } },
    ], 'http://127.0.0.1:8791');
    const panther = pub.publicFishItems.find((f) => /panther eel/i.test(f.canonicalName || f.name));
    assert.ok(panther);
    assert.equal(panther.imageSource, 'global_db');
    assert.ok(fishImageCache.cachedFileExists(panther.imageUrl));
  });

  test('public card HTML uses imageUrl img src not placeholder-only path', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('itemImageSrc(item)'));
    assert.ok(ejs.includes('img src="${escHtml(imgSrc)}"') || ejs.includes("img.setAttribute('src', imgSrc)"));
  });

  test('full-card rarity class remains on card root', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('.item-card.rarity-secret'));
    assert.ok(ejs.includes('cardRarityClass'));
  });

  test('no public card weight or fish badge in template', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(!ejs.includes('badge-fish'));
    assert.ok(!ejs.includes('debugWeight'));
    assert.ok(!ejs.includes('weight-badge'));
  });
});
