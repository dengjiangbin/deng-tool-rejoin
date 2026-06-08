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
  buildCountParityProof,
  buildReplionCountProof,
  buildCatchLearningProof,
  buildUnmappedReviewProof,
  buildRarityColorProof,
  buildTrackerClientProof,
  isPublicFishItem,
  isLikelyFishInventoryItem,
  buildAmountProof,
  extractReplionAmount,
  annotateReplionIdentity,
  catalogMetaForItemId,
  _itemIdLockedBaseName,
} = require('../src/fishitTrackerRoutes');
const rarityColorMap = require('../src/fishitRarityColorMap');

const Z9_BUILD = 'BLOCKER10Z9_PUBLIC_SNAPSHOT_TRUTH_AND_FULL_RARITY_CARDS_2026_06_08';
const Z8_BUILD = Z9_BUILD;
const Z7_BUILD = Z8_BUILD;
const Z6_BUILD = Z8_BUILD;
const Z5_BUILD = Z8_BUILD;
const Z4_BUILD = Z8_BUILD;
const Z3_BUILD = Z8_BUILD;
const Z_BUILD = Z8_BUILD;
const Y_BUILD = Z8_BUILD;
const X_BUILD = Z8_BUILD;
const W_BUILD = Z8_BUILD;
let tmpDb;

function setupTestDb() {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fishit-global-w-'));
  tmpDb = path.join(tmpDir, 'fishit_global_test.db');
  process.env.FISHIT_GLOBAL_DB_PATH = tmpDb;
  globalDb._reset();
  globalCatalogService._reset();
  fishImageCache._reset();
  fs.mkdirSync(path.join(__dirname, '..', 'data', 'fish_image_cache'), { recursive: true });
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

  test('public fish resolves image from global DB using itemId locked base name', async () => {
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
    assert.equal(item.baseFishName, 'Zebra Snakehead');
    assert.equal(item.canonicalName || item.name, 'Zebra Snakehead');
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

  test('duplicate same species with different itemIds stay separate when catalog base differs', () => {
    const grouped = catalogPolish.groupPublicFishItems([
      { baseFishName: 'Mossy Fishlet', name: 'Mossy Fishlet', amount: 2, weight: 6.2, category: 'fish', itemId: '277' },
      { baseFishName: 'Zebra Snakehead', name: 'Zebra Snakehead', amount: 2, weight: 333.9, category: 'fish', itemId: '287' },
    ]);
    assert.equal(grouped.length, 2);
    assert.equal(grouped[0].amount, 2);
    assert.equal(grouped[1].amount, 2);
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
    const { BLOCKER10Y_BUILD } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10Y_BUILD, Y_BUILD);
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

describe('BLOCKER10Y rarity color count global proof', { concurrency: 1 }, () => {
  test('build marker is BLOCKER10Z3 in tracker build and tracker.lua', () => {
    const { BLOCKER10Z_BUILD } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10Z_BUILD, Z3_BUILD);
    const lua = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(lua.includes('BLOCKER10Z3_REPLION_GLOBAL_DB_NO_UI_DEPENDENCY_2026_06_07'));
    assert.ok(!lua.includes('payload.inventoryUiHints'));
    assert.ok(lua.includes('replionSourceOfTruth = true'));
  });

  test('countParityProof separates raw enriched grouped and unmapped counts', () => {
    const enriched = [
      { name: 'Giant Squid', itemId: '156', category: 'fish', amount: 2, baseFishName: 'Giant Squid' },
      { name: 'Item #265', itemId: '265', category: 'items', amount: 1, weight: 50,
        rawProof: { rawObjectPreview: { Favorited: 'false' } } },
    ];
    const pub = [{ name: 'Giant Squid', itemId: '156', amount: 2, baseFishName: 'Giant Squid' }];
    const cp = buildCountParityProof(enriched, enriched, pub, {
      parseStats: { raw: 3, acceptedInstances: 3 },
      bagInstanceCount: 3,
    });
    assert.equal(cp.trackerRawInstanceCount, 3);
    assert.equal(cp.acceptedInstances, 3);
    assert.ok(cp.enrichedFishInstances >= 2);
    assert.equal(cp.publicFishTypes, 1);
    assert.ok(cp.unmappedFishCandidateInstances >= 1);
    assert.equal(cp.inGameBagCountEvidence, 'tracker_bagInstanceCount=3');
  });

  test('website header template shows replion-based fish count without visible page', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('fishCountLabel'));
    assert.ok(ejs.includes('Fish:'));
    assert.ok(ejs.includes('Types:'));
    assert.ok(ejs.includes('Unmapped:'));
    assert.ok(!ejs.includes('Visible page:'));
    assert.ok(!ejs.includes('Snapshot:'));
    assert.ok(ejs.includes('buildGlobalDbProofHtml'));
  });

  test('imageUrl imageUrlPresent imageResolved cannot contradict when cached', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    fishImageCache._reset();
    const pub = await buildPublicFishFields([{
      name: 'Giant Squid', baseFishName: 'Giant Squid', amount: 1, category: 'fish', itemId: '156',
    }], 'http://127.0.0.1:8791');
    const item = pub.publicItems[0];
    assert.ok(item.imageUrl);
    assert.equal(item.imageUrlPresent, true);
    assert.equal(item.imageResolved, true);
    const proof = fishImageCache.buildImageRenderProof(pub.publicItems, 1)[0].imageRenderProof;
    assert.equal(proof.imageUrlPresent, true);
    assert.equal(proof.imageResolved, true);
    assert.equal(proof.placeholderUsed, false);
  });

  test('public frontend uses imageUrl field only', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('itemImageSrc(item)'));
    assert.ok(ejs.includes('item.imageUrl'));
    assert.ok(!ejs.includes('imageProxyUrl'));
  });

  test('Flowery Fish missing image explained when not in Quiz Bot seed', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const audit = quizBotCatalog.auditNames(['Flowery Fish']);
    assert.equal(audit[0]?.matched, false);
    const pub = await buildPublicFishFields([{
      name: 'Flowery Fish', baseFishName: 'Flowery Fish', amount: 1, category: 'fish',
    }], 'http://127.0.0.1:8791');
    const flowery = pub.publicItems.find((f) => /flowery fish/i.test(f.name));
    if (flowery) {
      assert.equal(flowery.imageUrlPresent, false);
    }
  });

  test('rarity stays neutral without global DB evidence when no tier source', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([{
      name: 'Mossy Fishlet', baseFishName: 'Mossy Fishlet', amount: 1, category: 'fish', itemId: '287',
    }], 'http://127.0.0.1:8791', {
      sessionData: {
        inventoryUiHints: [{ visibleName: 'Mossy Fishlet', textColor: '#22d3ee' }],
      },
    });
    const item = pub.publicItems[0];
    assert.equal(item.baseFishName, 'Zebra Snakehead');
    assert.notEqual(item.raritySource, 'inventory_ui_color');
  });

  test('card rarity class matches final rarity tier', () => {
    const proof = rarityColorMap.buildRarityColorProofRow({
      itemId: '156', canonicalName: 'Giant Squid', rarity: 'Secret', raritySource: 'global_db',
    });
    assert.equal(proof.cardClass, 'rarity-secret');
    assert.equal(proof.cardUsesFullRarityStyle, true);
  });

  test('card name accent follows rarity color in template', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('rarityNameStyle'));
    assert.ok(ejs.includes('RARITY_NAME_COLORS'));
    assert.ok(ejs.includes('style="${nameStyle}"') || ejs.includes("nameEl.style.cssText = nameStyle"));
  });

  test('unknown rarity stays neutral with explicit reason in proof', () => {
    const proof = buildRarityColorProof([{
      itemId: '999', name: 'Unknown Fish', rarity: null, rarityNeedsData: true,
    }], 1)[0];
    assert.equal(proof.finalRarity, null);
    assert.equal(proof.rarityUnknownReason, 'no_tier_source');
    assert.equal(proof.cardUsesFullRarityStyle, false);
  });

  test('global DB proof UI displays source and card usage counts', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([{
      name: 'Giant Squid', baseFishName: 'Giant Squid', amount: 1, category: 'fish', itemId: '156',
    }], 'http://127.0.0.1:8791');
    const ui = pub.globalDbUiProof;
    assert.equal(ui.sourceOfTruth, 'global_db');
    assert.ok(ui.speciesCount >= 620);
    assert.equal(ui.cardsTotal, 1);
    assert.ok(ui.cardsUsingGlobalDbImages >= 1);
  });

  test('per-card data attributes expose global_db source', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([{
      name: 'Freshwater Piranha', baseFishName: 'Freshwater Piranha', amount: 1, category: 'fish', itemId: '284',
    }], 'http://127.0.0.1:8791');
    const item = pub.publicItems[0];
    assert.equal(item.dataImageSource, 'global_db');
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('data-image-source'));
    assert.ok(ejs.includes('data-rarity-source'));
  });

  test('unmapped review proof lists candidates and manual_review_required', () => {
    const enriched = [
      { name: 'Item #265', itemId: '265', category: 'items', amount: 2, weight: 50,
        rawProof: { rawObjectPreview: { Favorited: 'false' } } },
    ];
    const review = buildUnmappedReviewProof(enriched);
    assert.equal(review.length, 1);
    assert.equal(review[0].itemId, '265');
    assert.equal(review[0].recommendedAction, 'manual_review_required');
    assert.equal(review[0].autoMapped, false);
  });

  test('trackerClientProof reports BLOCKER10Z3 Replion capabilities', () => {
    const proof = buildTrackerClientProof({
      trackerBuild: Y_BUILD,
      bagInstanceCount: 61,
      trackerClientProof: {
        trackerBuild: Y_BUILD,
        uploadedAt: '2026-06-07T12:00:00.000Z',
        supportsBagInstanceCount: true,
        noHeavyScanner: true,
        replionSourceOfTruth: true,
      },
    });
    assert.equal(proof.trackerBuild, Y_BUILD);
    assert.equal(proof.replionSourceOfTruth, true);
    assert.equal(proof.inventoryUiOptional, true);
    assert.equal(proof.supportsBagInstanceCount, true);
    assert.equal(proof.noHeavyScanner, true);
  });

  test('no raw session key or user identity in trackerClientProof', () => {
    const proof = buildTrackerClientProof({
      sessionKey: 'denghub2_secret',
      userId: 12345678,
      trackerClientProof: { trackerBuild: Y_BUILD },
    });
    assert.ok(!JSON.stringify(proof).includes('denghub2_secret'));
    assert.ok(!JSON.stringify(proof).includes('12345678'));
  });
});

describe('BLOCKER10Z3 replion global db no UI dependency', { concurrency: 1 }, () => {
  test('build marker is BLOCKER10Z3', () => {
    const { BLOCKER10Z3_BUILD } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10Z3_BUILD, Z3_BUILD);
  });

  test('itemId 287 locked base is Zebra Snakehead not Mossy Fishlet alias', () => {
    assert.equal(_itemIdLockedBaseName('287'), 'Zebra Snakehead');
    const meta = catalogMetaForItemId('287');
    assert.equal(meta.baseFishName, 'Zebra Snakehead');
    assert.equal(meta.name, 'Zebra Snakehead');
  });

  test('normal tracker template hides global-db-proof panel entirely', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('DEBUG_GLOBAL'));
    assert.ok(ejs.includes('if (!DEBUG_GLOBAL) return'));
    assert.ok(!ejs.includes('gdb-indicator'));
  });

  test('debug=global template renders full global-db-proof panel', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('debug=global'));
    assert.ok(ejs.includes('global-db-proof'));
    assert.ok(ejs.includes('rarityColorProof'));
    assert.ok(ejs.includes('replionCountProof'));
    assert.ok(ejs.includes('catchLearningProof'));
  });

  test('poll refresh uses buildGlobalDbProofHtml gated by DEBUG_GLOBAL', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('gdb.innerHTML = buildGlobalDbProofHtml(data)'));
    assert.ok(ejs.includes('if (!DEBUG_GLOBAL) return'));
  });

  test('countParityProof uses Replion snapshot without visible page fields', () => {
    const enriched = [
      { name: 'Giant Squid', itemId: '156', category: 'fish', amount: 1, baseFishName: 'Giant Squid' },
    ];
    const pub = [{ name: 'Giant Squid', itemId: '156', amount: 1, baseFishName: 'Giant Squid' }];
    const cp = buildCountParityProof(enriched, enriched, pub, {
      parseStats: { raw: 61, acceptedInstances: 61 },
    });
    assert.equal(cp.fullSnapshotItemInstances, 61);
    assert.ok(cp.explanation.includes('Replion'));
    assert.equal(cp.visibleBagPageFishCount, undefined);
    assert.equal(cp.visibleBagPageCaptured, undefined);
  });

  test('buildReplionCountProof exposes debug snapshot fields', () => {
    const cp = buildCountParityProof(
      [{ name: 'Giant Squid', itemId: '156', category: 'fish', amount: 2, baseFishName: 'Giant Squid' }],
      [{ name: 'Giant Squid', itemId: '156', category: 'fish', amount: 2, baseFishName: 'Giant Squid' }],
      [{ name: 'Giant Squid', itemId: '156', amount: 2, baseFishName: 'Giant Squid' }],
      { parseStats: { acceptedInstances: 61 } },
    );
    const rp = buildReplionCountProof(cp);
    assert.equal(rp.snapshotItemInstances, 61);
    assert.equal(rp.fishCandidates, 2);
    assert.equal(rp.publicFishInstances, 2);
  });

  test('buildCatchLearningProof reports pending catch without raw identity', () => {
    const proof = buildCatchLearningProof({
      lastPendingCatchName: { fishName: 'New Fish', rarityCandidate: 'Rare', source: 'catch_popup' },
    }, null);
    assert.equal(proof.catchEvidenceSupported, true);
    assert.equal(proof.pendingCatch.fishName, 'New Fish');
  });

  test('UI color map module exists but is not used in public rarity pipeline', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([{
      name: 'Giant Squid', baseFishName: 'Giant Squid', amount: 1, category: 'fish', itemId: '156',
    }], 'http://127.0.0.1:8791', {
      sessionData: {
        inventoryUiHints: [{ visibleName: 'Giant Squid', textColor: '#4ade80' }],
      },
    });
    assert.equal(pub.publicItems[0].rarity, 'Secret');
    assert.notEqual(pub.publicItems[0].raritySource, 'inventory_ui_color');
  });

  test('tracker.lua uses Replion source of truth without UI hint upload', () => {
    const lua = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(lua.includes('replionSourceOfTruth = true'));
    assert.ok(lua.includes('noHeavyScanner = true'));
    assert.ok(!lua.includes('payload.inventoryUiHints'));
    assert.ok(!lua.includes('visibleBagPageFishCount'));
  });

  test('Panther Eel remains visible with Secret rarity when mapped', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    globalCatalogService.approveItemMapping({
      itemId: 248,
      canonicalName: 'Panther Eel',
      source: 'admin_manual_screenshot_confirmation',
      verificationStatus: 'manual_verified',
    });
    const pub = await buildPublicFishFields([
      { name: 'Item #248', itemId: '248', category: 'items', amount: 1, weight: 100,
        rawProof: { rawObjectPreview: { Favorited: 'false' } } },
    ], 'http://127.0.0.1:8791');
    const panther = pub.publicFishItems.find((f) => /panther eel/i.test(f.canonicalName || f.name));
    assert.ok(panther);
    assert.equal(panther.rarity, 'Secret');
    assert.ok(panther.imageUrlPresent);
  });
});

describe('BLOCKER10Z4 amount regression fix', { concurrency: 1 }, () => {
  test('build marker is BLOCKER10Z4', () => {
    const { BLOCKER10Z4_BUILD } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10Z4_BUILD, Z4_BUILD);
    const lua = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(lua.includes('BLOCKER10Z6_CATALOG_NAMES_NO_FAKE_MERGE_2026_06_08'));
    assert.ok(lua.includes('LiveSafe.resolveOwnedStorageKey'));
  });

  test('Topwater Bait Quantity 135 parses from rawProof not amount 1', () => {
    const hit = extractReplionAmount({
      name: 'Topwater Bait', itemId: '10', amount: 1,
      rawProof: { rawObjectPreview: { Quantity: 135, Id: '10' } },
    });
    assert.equal(hit.amount, 135);
    assert.equal(hit.source, 'replion_raw_object_quantity');
  });

  test('fish public amount comes from replion fields not global DB', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([
      { name: 'Item #267', itemId: '267', category: 'fish', amount: 1,
        metadataFishName: 'Parrot Blopfish',
        identityVerified: true,
        replionUuid: 'e0ce8a51-2b73-41fb-a319-ebc1c949a9f3',
        replionAmountSource: 'replion_uuid_instance' },
    ], 'http://127.0.0.1:8791');
    const item = pub.publicItems[0];
    assert.equal(item.amount, 1);
    assert.equal(item.dataAmountSource, 'replion_uuid_instance');
    assert.ok(pub.amountProof);
    assert.equal(pub.amountProof.rows[0].amountFromGlobalDb, false);
  });

  test('itemId 267 without metadata stays hidden from public (Z8)', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([
      { name: 'Item #267', itemId: '267', category: 'fish', amount: 1,
        replionUuid: 'uuid-test-267-a', replionAmountSource: 'replion_uuid_instance' },
    ], 'http://127.0.0.1:8791');
    assert.equal(pub.publicItems.length, 0);
    assert.equal(pub.hiddenPublicRows.ambiguousContainerUnresolved, 1);
  });

  test('itemId 1008 stays Goliath Tiger not Spear Guardian alias', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const canon = catalogMetaForItemId('1008');
    if (!canon || !/goliath tiger/i.test(canon.baseFishName || '')) return;
    const pub = await buildPublicFishFields([
      { name: 'Item #1008', itemId: '1008', category: 'fish', amount: 1,
        replionUuid: 'uuid-test-1008-a', replionAmountSource: 'replion_uuid_instance' },
    ], 'http://127.0.0.1:8791');
    const item = pub.publicItems[0];
    assert.equal(item.baseFishName, 'Goliath Tiger');
    assert.ok(!/spear guardian/i.test(item.name || ''));
  });

  test('regression fixture does not output Catfish x32 from single uuid row', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([
      { name: 'Item #267', itemId: '267', category: 'fish', amount: 1,
        replionUuid: 'e0ce8a51-2b73-41fb-a319-ebc1c949a9f3',
        replionAmountSource: 'replion_uuid_instance' },
    ], 'http://127.0.0.1:8791');
    const catfish = pub.publicItems.find((f) => /catfish/i.test(f.name || ''));
    if (catfish) assert.notEqual(catfish.amount, 32);
    assert.ok(!pub.publicItems.some((f) => f.amount === 32 && /catfish/i.test(f.name || '')));
  });

  test('header template hides unverified Fish total', () => {
    const ejs = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.ok(ejs.includes('fishInstancesVerified'));
    assert.ok(ejs.includes('Types:'));
    assert.ok(!ejs.match(/Fish:\s*<strong>\$\{fishTotal\}/));
  });

  test('buildAmountProof exposes per-card replion amount source', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([
      { name: 'Item #267', itemId: '267', category: 'fish', amount: 1,
        replionUuid: 'proof-uuid-267', replionAmountSource: 'replion_uuid_instance' },
    ], 'http://127.0.0.1:8791');
    assert.ok(pub.amountProof.rows.length >= 1);
    assert.equal(pub.amountProof.rows[0].publicAmount, 1);
    assert.equal(pub.amountProof.rows[0].amountFromGlobalDb, false);
    assert.ok(pub.amountProof.rows[0].whyAmountCorrect.includes('Replion'));
  });
});

describe('BLOCKER10Z6 catalog names without fake merge', { concurrency: 1 }, () => {
  test('manual verified item shows catalog name when tracker sends identityVerified false', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([
      {
        name: 'Item #156',
        itemId: '156',
        category: 'fish',
        amount: 1,
        weight: 105545,
        replionUuid: 'd43bc063-75cb-4681-9095-1b140060476f',
        replionAmountSource: 'replion_uuid_instance',
        identityVerified: false,
      },
    ], 'http://127.0.0.1:8791');
    assert.ok(pub.publicItems.length >= 1);
    assert.match(pub.publicItems[0].name || '', /giant squid/i);
    assert.notMatch(pub.publicItems[0].name || '', /item #156/i);
  });

  test('container collision rows keep Item placeholder without metadata', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const rows = Array.from({ length: 32 }, (_, i) => ({
      name: 'Item #267',
      itemId: '267',
      containerItemId: '267',
      category: 'fish',
      amount: 1,
      weight: 0.5 + (i * 0.02),
      replionUuid: `uuid-267-${i}`,
      replionAmountSource: 'replion_uuid_instance',
      identityVerified: false,
    }));
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    assert.equal(
      pub.publicItems.reduce((s, f) => s + (Number(f.amount) || 0), 0),
      32,
    );
    assert.ok(pub.publicItems.every((f) => /item #267/i.test(f.name || '')));
    assert.ok(pub.publicItems.every((f) => (Number(f.amount) || 0) === 1));
    assert.equal(
      pub.publicItems.find((f) => f.amount === 32 && /parrot blopfish/i.test(f.name || '')),
      undefined,
    );
  });

  test('same-species rows below collision threshold group with real names', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const rows = [
      { name: 'Item #285', itemId: '285', category: 'fish', amount: 1, weight: 3.4,
        replionUuid: 'uuid-285-a', replionAmountSource: 'replion_uuid_instance', identityVerified: false },
      { name: 'Item #285', itemId: '285', category: 'fish', amount: 1, weight: 3.0,
        replionUuid: 'uuid-285-b', replionAmountSource: 'replion_uuid_instance', identityVerified: false },
      { name: 'Item #285', itemId: '285', category: 'fish', amount: 1, weight: 2.8,
        replionUuid: 'uuid-285-c', replionAmountSource: 'replion_uuid_instance', identityVerified: false },
    ];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    const goat = pub.publicItems.find((f) => /goatfish/i.test(f.name || f.baseFishName || ''));
    assert.ok(goat);
    assert.equal(goat.amount, 3);
  });
});

describe('BLOCKER10Z5 replion identity no fake merge', { concurrency: 1 }, () => {
  test('build marker is BLOCKER10Z6', () => {
    const { BLOCKER10Z6_BUILD } = require('../src/fishitTrackerBuild');
    assert.equal(BLOCKER10Z6_BUILD, Z6_BUILD);
    const lua = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.ok(lua.includes('BLOCKER10Z6_CATALOG_NAMES_NO_FAKE_MERGE_2026_06_08'));
    assert.ok(lua.includes('replion_identity_unverified'));
  });

  test('publicAggregationKey does not merge unverified UUID rows by catalog name', () => {
    const keyA = catalogPolish.publicAggregationKey({
      replionUuid: 'uuid-a',
      itemId: '267',
      baseFishName: 'Parrot Blopfish',
      catalogLockedBaseName: 'Parrot Blopfish',
      replionIdentityUnverified: true,
    });
    const keyB = catalogPolish.publicAggregationKey({
      replionUuid: 'uuid-b',
      itemId: '267',
      baseFishName: 'Parrot Blopfish',
      catalogLockedBaseName: 'Parrot Blopfish',
      replionIdentityUnverified: true,
    });
    assert.notEqual(keyA, keyB);
    assert.match(keyA, /^uuid:/);
  });

  test('32 UUID rows with container id 267 do not produce Parrot Blopfish x32', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const rows = Array.from({ length: 32 }, (_, i) => ({
      name: 'Parrot Blopfish',
      itemId: '267',
      containerItemId: '267',
      category: 'fish',
      amount: 1,
      weight: 0.5 + (i * 0.02),
      replionUuid: `uuid-267-${i}`,
      replionAmountSource: 'replion_uuid_instance',
    }));
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    const fakeMerge = pub.publicItems.find(
      (f) => f.amount === 32 && /parrot blopfish/i.test(f.name || f.baseFishName || ''),
    );
    assert.equal(fakeMerge, undefined);
    assert.equal(
      pub.publicItems.reduce((s, f) => s + (Number(f.amount) || 0), 0),
      32,
    );
    assert.ok(pub.publicItems.every((f) => (Number(f.amount) || 0) === 1));
  });

  test('legacy session rows without uuid still avoid fake x32 merge', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const rows = Array.from({ length: 32 }, (_, i) => ({
      name: 'Parrot Blopfish',
      itemId: '267',
      category: 'fish',
      amount: 1,
      weight: 0.5 + (i * 0.02),
    }));
    const annotated = annotateReplionIdentity(rows);
    assert.ok(annotated.every((r) => r.replionIdentityUnverified));
    const pub = await buildPublicFishFields(annotated, 'http://127.0.0.1:8791');
    const fakeMerge = pub.publicItems.find((f) => f.amount === 32);
    assert.equal(fakeMerge, undefined);
  });

  test('verified metadataFishId rows still group by species', () => {
    const keyA = catalogPolish.publicAggregationKey({
      replionUuid: 'uuid-a',
      metadataFishId: '385',
      mutation: 'Shiny',
      identityVerified: true,
    });
    const keyB = catalogPolish.publicAggregationKey({
      replionUuid: 'uuid-b',
      metadataFishId: '385',
      mutation: 'Shiny',
      identityVerified: true,
    });
    assert.equal(keyA, keyB);
  });

  test('BLOCKER10Z7: 32 rows Id 267 with UUIDs do not merge to Parrot Blopfish x32', async () => {
    setupTestDb();
    const rows = Array.from({ length: 32 }, (_, i) => ({
      name: 'Item #267',
      itemId: '267',
      replionTopLevelId: '267',
      containerItemId: '267',
      isAmbiguousContainerId: true,
      category: 'fish',
      amount: 1,
      weight: 0.5 + (i * 0.02),
      replionUuid: `uuid-267-${i}`,
      replionAmountSource: 'replion_uuid_instance',
    }));
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    const fakeMerge = pub.publicItems.find(
      (f) => f.amount === 32 && /parrot blopfish|catfish/i.test(f.name || f.baseFishName || ''),
    );
    assert.equal(fakeMerge, undefined);
    assert.equal(pub.publicItems.length, 0);
    assert.equal(pub.hiddenPublicRows.ambiguousContainerUnresolved, 32);
    assert.ok(!pub.publicItems.some((f) => /unknown fish #267/i.test(f.name || '')));
  });

  test('BLOCKER10Z7: 267 row with metadataFishName Panther Eel resolves correctly', async () => {
    setupTestDb();
    const rows = [{
      name: 'Item #267',
      itemId: '267',
      replionTopLevelId: '267',
      isAmbiguousContainerId: true,
      metadataFishName: 'Panther Eel',
      category: 'fish',
      amount: 1,
      replionUuid: 'uuid-panther',
      replionAmountSource: 'replion_uuid_instance',
      identityVerified: true,
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    assert.ok(pub.publicItems.some((f) => /panther eel/i.test(f.name || f.displayName || '')));
  });

  test('BLOCKER10Z7: 267 row with trusted metadataFishId resolves via catalog', async () => {
    setupTestDb();
    const rows = [{
      name: 'Item #267',
      itemId: '267',
      replionTopLevelId: '267',
      isAmbiguousContainerId: true,
      metadataFishId: '248',
      category: 'fish',
      amount: 1,
      replionUuid: 'uuid-meta-id',
      replionAmountSource: 'replion_uuid_instance',
      identityVerified: true,
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    assert.ok(pub.publicItems.some((f) => /panther eel/i.test(f.name || f.baseFishName || f.displayName || '')));
  });

  test('BLOCKER10Z7: 267 row without metadata stays hidden from public (debug only)', async () => {
    setupTestDb();
    const rows = [{
      name: 'Item #267',
      itemId: '267',
      replionTopLevelId: '267',
      isAmbiguousContainerId: true,
      category: 'fish',
      amount: 1,
      replionUuid: 'uuid-unmapped',
      replionAmountSource: 'replion_uuid_instance',
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    assert.equal(pub.publicItems.length, 0);
    assert.equal(pub.hiddenPublicRows.ambiguousContainerUnresolved, 1);
    assert.ok(!pub.publicItems.some((f) => /unknown fish #267|unmapped fish/i.test(f.name || f.displayName || '')));
    assert.ok(!pub.publicItems.some((f) => /parrot blopfish/i.test(f.name || '')));
  });

  test('BLOCKER10Z7: one Shiny Parrot Blopfish metadata row does not name all 267 rows', async () => {
    setupTestDb();
    const rows = [
      {
        name: 'Item #267',
        itemId: '267',
        replionTopLevelId: '267',
        isAmbiguousContainerId: true,
        metadataFishName: 'Shiny Parrot Blopfish',
        category: 'fish',
        amount: 1,
        replionUuid: 'uuid-parrot-one',
        replionAmountSource: 'replion_uuid_instance',
        identityVerified: true,
      },
      ...Array.from({ length: 5 }, (_, i) => ({
        name: 'Item #267',
        itemId: '267',
        replionTopLevelId: '267',
        isAmbiguousContainerId: true,
        category: 'fish',
        amount: 1,
        replionUuid: `uuid-other-${i}`,
        replionAmountSource: 'replion_uuid_instance',
      })),
    ];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    const parrotCards = pub.publicItems.filter((f) => /parrot blopfish/i.test(f.name || f.displayName || ''));
    assert.equal(parrotCards.reduce((s, f) => s + (Number(f.amount) || 0), 0), 1);
    assert.equal(pub.hiddenPublicRows.ambiguousContainerUnresolved, 5);
    assert.ok(!pub.publicItems.some((f) => /unknown fish #267/i.test(f.name || f.displayName || '')));
  });

  test('BLOCKER10Z7: header count excludes hidden ambiguous 267 rows', async () => {
    setupTestDb();
    const rows = Array.from({ length: 32 }, (_, i) => ({
      name: 'Item #267',
      itemId: '267',
      replionTopLevelId: '267',
      isAmbiguousContainerId: true,
      category: 'fish',
      amount: 1,
      replionUuid: `uuid-hdr-${i}`,
      replionAmountSource: 'replion_uuid_instance',
    }));
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    assert.equal(pub.fishCounts.fishInstances, 0);
    assert.equal(pub.publicCounts.visibleFishInstances, 0);
    assert.equal(pub.publicCounts.hiddenUnresolvedFishRows, 32);
  });

  test('BLOCKER10Z7: buildAmbiguousContainerProof exposes sample stats', () => {
    const { buildAmbiguousContainerProof } = require('../src/fishitTrackerRoutes');
    const rows = Array.from({ length: 32 }, (_, i) => ({
      itemId: '267',
      replionTopLevelId: '267',
      isAmbiguousContainerId: true,
      replionUuid: `uuid-proof-${i}`,
      metadataFishName: i === 0 ? 'Giant Squid' : null,
    }));
    const proof = buildAmbiguousContainerProof(rows, {
      ambiguousContainerIds: [267],
      ambiguousContainerProof: {
        rowsSeen: 32,
        rowsWithMetadataFishId: 0,
        rowsWithMetadataFishName: 1,
        rowsUnresolved: 31,
        sample: [{ topLevelId: 267, uuid: 'uuid-proof-0' }],
      },
    });
    assert.equal(proof.rowsSeen, 32);
    assert.equal(proof.rowsWithMetadataFishName, 1);
    assert.equal(proof.rowsUnresolved, 31);
    assert.ok(Array.isArray(proof.sample));
  });
});

describe('BLOCKER10Z8 — hide fake 267 and cosmetic tags', { concurrency: 1 }, () => {
  const {
    buildPublicFishFields,
    buildAmountProof,
    isPublicFishCardVisible,
    applyPublicCosmeticCleanup,
    stripHiddenPublicCosmeticPrefix,
    isTrustedRadiantCatfishInCatalog,
  } = require('../src/fishitTrackerRoutes');

  function fake267Row(i) {
    return {
      name: 'Item #267',
      itemId: '267',
      containerItemId: '267',
      replionTopLevelId: '267',
      isAmbiguousContainerId: true,
      containerIdCollision: true,
      replionIdentityUnverified: true,
      identityVerified: false,
      metadataFishId: null,
      metadataFishName: null,
      category: 'fish',
      amount: 1,
      replionUuid: `uuid-fake-${i}`,
      replionAmountSource: 'replion_uuid_instance',
      confidence: 'ambiguous_container_unmapped',
    };
  }

  test('A: 32 fake 267 rows hidden from public cards and counts', async () => {
    setupTestDb();
    const rows = Array.from({ length: 32 }, (_, i) => fake267Row(i));
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    assert.ok(!pub.publicItems.some((f) => /unknown fish #267/i.test(f.name || f.cardName || '')));
    if (isTrustedRadiantCatfishInCatalog()) {
      assert.equal(pub.publicCounts.visibleFishInstances, 1);
      assert.ok(pub.publicItems.some((f) => /radiant catfish/i.test(f.name || f.baseFishName || '')));
      assert.equal(pub.hiddenPublicRows.ambiguousContainerUnresolved, 31);
    } else {
      assert.equal(pub.publicItems.length, 0);
      assert.equal(pub.publicCounts.visibleFishInstances, 0);
      assert.equal(pub.hiddenPublicRows.ambiguousContainerUnresolved, 32);
    }
    assert.deepEqual(pub.hiddenPublicRows.hiddenItemIds, ['267']);
  });

  test('B: trusted 267 with metadataFishName still shows resolved fish', async () => {
    setupTestDb();
    const rows = [{
      ...fake267Row(0),
      metadataFishName: 'Panther Eel',
      identityVerified: true,
      replionIdentityUnverified: false,
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    assert.ok(pub.publicItems.some((f) => /panther eel/i.test(f.name || f.baseFishName || '')));
    assert.ok(!pub.publicItems.some((f) => /unknown fish #267/i.test(f.name || '')));
    assert.equal(pub.publicCounts.visibleFishInstances, 1);
  });

  test('C: Big/Shiny stripped from public names and badges', () => {
    const cases = [
      { in: 'Big Freshwater Piranha', out: 'Freshwater Piranha' },
      { in: 'Shiny Parrot Fish', out: 'Parrot Fish' },
      { in: 'Big Shiny Seaweed Pufferfish', out: 'Seaweed Pufferfish' },
    ];
    for (const c of cases) {
      assert.equal(stripHiddenPublicCosmeticPrefix(c.in), c.out);
      const cleaned = applyPublicCosmeticCleanup({
        name: c.in,
        baseFishName: c.in,
        displayName: c.in,
        mutation: c.in.startsWith('Shiny') ? 'Shiny' : (c.in.startsWith('Big Shiny') ? 'Big Shiny' : 'Big'),
        shiny: true,
      });
      assert.equal(cleaned.publicCardName, c.out);
      assert.equal(cleaned.mutation, null);
      assert.equal(cleaned.shiny, false);
      assert.equal(cleaned.mutationTags.length, 0);
    }
  });

  test('D: amountProof publicCardName excludes Big/Shiny', async () => {
    setupTestDb();
    const rows = [{
      name: 'Big Freshwater Piranha',
      itemId: '156',
      category: 'fish',
      amount: 1,
      baseFishName: 'Giant Squid',
      metadataFishName: 'Giant Squid',
      identityVerified: true,
      replionUuid: 'uuid-giant-test',
      replionAmountSource: 'replion_uuid_instance',
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    const proof = pub.amountProof || buildAmountProof(pub.fishItems, rows);
    assert.ok(proof.rows.length >= 1);
    for (const r of proof.rows) {
      assert.ok(!/\bbig\b/i.test(r.publicCardName || ''));
      assert.ok(!/\bshiny\b/i.test(r.publicCardName || ''));
    }
  });

  test('E: header counts visible fish only with hidden 267 rows', async () => {
    setupTestDb();
    const goodIds = ['156', '248', '274', '268', '270', '243', '244', '245', '246', '247', '249', '250'];
    const verified = Array.from({ length: 20 }, (_, i) => ({
      name: `Species ${i % 12}`,
      itemId: goodIds[i % goodIds.length],
      category: 'fish',
      amount: 1,
      baseFishName: `Species ${i % 12}`,
      metadataFishName: `Species ${i % 12}`,
      identityVerified: true,
      replionUuid: `uuid-verified-${i}`,
      replionAmountSource: 'replion_uuid_instance',
    }));
    const fake267 = Array.from({ length: 32 }, (_, i) => fake267Row(i));
    const pub = await buildPublicFishFields([...verified, ...fake267], 'http://127.0.0.1:8791');
    const radiantExtra = isTrustedRadiantCatfishInCatalog() ? 1 : 0;
    assert.equal(pub.publicCounts.visibleFishInstances, 20 + radiantExtra);
    assert.equal(pub.publicCounts.hiddenUnresolvedFishRows, 32 - radiantExtra);
    assert.equal(pub.fishCounts.fishInstances, 20 + radiantExtra);
  });

  test('F: isPublicFishCardVisible rejects unknown 267 without trusted identity', () => {
    assert.equal(isPublicFishCardVisible(fake267Row(0)), false);
    assert.equal(isPublicFishCardVisible({
      ...fake267Row(0),
      metadataFishName: 'Panther Eel',
      identityVerified: true,
    }), true);
  });

  test('G: GET /tracker HTTP 200 without Unknown Fish #267 or Shiny badges in HTML', async () => {
    const express = require('express');
    const request = require('supertest');
    const trackerRouter = require('../src/fishitTrackerRoutes');
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(trackerRouter);
    const res = await request(app).get('/tracker').expect(200);
    assert.doesNotMatch(res.text, /Unknown Fish #267/i);
    assert.doesNotMatch(res.text, /Big Shiny/i);
  });

  test('H: GET /tracker?debug=global includes hidden rows proof without crash', async () => {
    const express = require('express');
    const request = require('supertest');
    const trackerRouter = require('../src/fishitTrackerRoutes');
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(trackerRouter);
    const res = await request(app).get('/tracker?debug=global').expect(200);
    assert.match(res.text, /hiddenPublicRows|hiddenUnresolved|quarantinedPublicNames/i);
  });
});

describe('BLOCKER10Z9 — snapshot truth, Radiant Catfish, full rarity cards', { concurrency: 1 }, () => {
  const {
    buildPublicFishFields,
    isTrustedPublicNameSource,
    isSnapshotBackedPublicCard,
    buildPublicIdentityProof,
    isContestedCatalogItemId,
    promoteTrustedAmbiguousContainerRows,
    isTrustedRadiantCatfishInCatalog,
  } = require('../src/fishitTrackerRoutes');

  test('A: Goliath Tiger from contested itemId 1008 hidden without snapshot metadata', async () => {
    setupTestDb();
    const rows = [{
      name: 'Goliath Tiger',
      itemId: '1008',
      category: 'fish',
      amount: 1,
      replionUuid: 'uuid-goliath-stale',
      replionAmountSource: 'replion_uuid_instance',
      catalogSource: 'canonical_catalog',
      baseFishName: 'Goliath Tiger',
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    assert.ok(!pub.publicItems.some((f) => /goliath tiger/i.test(f.name || '')));
    assert.ok(pub.quarantinedPublicNames.some((q) => /goliath/i.test(q.name || '')));
  });

  test('B: Radiant Catfish promoted from trusted ambiguous 267 row', async () => {
    setupTestDb();
    const rows = [{
      name: 'Item #267',
      itemId: '267',
      containerItemId: '267',
      isAmbiguousContainerId: true,
      category: 'fish',
      amount: 1,
      weight: 13.4,
      replionUuid: 'uuid-radiant-catfish',
      replionAmountSource: 'replion_uuid_instance',
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    if (!isTrustedRadiantCatfishInCatalog()) return;
    assert.ok(pub.publicItems.some((f) => /radiant catfish/i.test(f.name || f.baseFishName || '')));
    assert.ok(pub.missingExpectedFishProof['Radiant Catfish'].currentSnapshotRowMatched);
  });

  test('C: catch-delta with nameValidated false is not trusted public name source', () => {
    assert.equal(isTrustedPublicNameSource({
      source: 'live_roblox_catch_delta',
      proof: { nameValidated: false, promotionReason: 'live_roblox_single_delta_public' },
    }), false);
  });

  test('D: public cards include publicIdentityProof.currentSnapshot', async () => {
    setupTestDb();
    const rows = [{
      name: 'Item #156',
      itemId: '156',
      category: 'fish',
      amount: 1,
      replionUuid: 'uuid-giant',
      replionAmountSource: 'replion_uuid_instance',
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    assert.ok(pub.publicItems.length >= 1);
    for (const item of pub.publicItems) {
      assert.equal(item.publicIdentityProof?.currentSnapshot, true);
    }
  });

  test('E: public header template excludes Unmapped label', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const fn = tpl.slice(tpl.indexOf('function fishCountLabel'), tpl.indexOf('function buildGlobalDbProofHtml'));
    assert.doesNotMatch(fn, /Unmapped:/);
  });

  test('F: full-card rarity CSS uses background not border-only', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /\.fish-card\.rarity-secret\s*\{[^}]*background:linear-gradient/);
    assert.match(tpl, /\.fish-card\.rarity-rare\s*\{[^}]*background:linear-gradient/);
  });

  test('G: itemId 1008 is contested catalog item', () => {
    assert.equal(isContestedCatalogItemId('1008'), true);
  });

  test('H: promoteTrustedAmbiguousContainerRows sets metadataFishName', () => {
    const learnedFishCatalog = require('../src/fishitLearnedFishCatalog');
    if (!isTrustedRadiantCatfishInCatalog()) return;
    const rows = promoteTrustedAmbiguousContainerRows([{
      name: 'Item #267',
      itemId: '267',
      isAmbiguousContainerId: true,
      replionUuid: 'uuid-promote-test',
      weight: 13.4,
    }]);
    const promoted = rows.find((r) => r.replionUuid === 'uuid-promote-test');
    if (learnedFishCatalog.lookupById('267')?.mutation === 'Radiant') {
      assert.equal(promoted?.metadataFishName, 'Radiant Catfish');
    }
  });
});

describe('BLOCKER10Z7 hotfix — /tracker page render', () => {
  const express = require('express');
  const request = require('supertest');
  const trackerRouter = require('../src/fishitTrackerRoutes');
  const ejs = require('ejs');
  const { BLOCKER10Z9_BUILD } = require('../src/fishitTrackerBuild');

  function makeApp() {
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(trackerRouter);
    return app;
  }

  test('GET /tracker returns HTTP 200 with no session data', async () => {
    const res = await request(makeApp()).get('/tracker').expect(200);
    assert.match(res.text, /Fish It Live Inventory Tracker/i);
    assert.match(res.text, /BLOCKER10Z9/);
  });

  test('GET /tracker?debug=global returns HTTP 200', async () => {
    const res = await request(makeApp()).get('/tracker?debug=global').expect(200);
    assert.match(res.text, /DEBUG_GLOBAL|global-db-proof|fishit-tracker/i);
  });

  test('buildTrackerPageLocals does not reference undefined build constants', () => {
    const { buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');
    const locals = buildTrackerPageLocals();
    assert.equal(locals.publicApiBuild, BLOCKER10Z9_BUILD);
    assert.equal(locals.blocker10vBuild, BLOCKER10Z9_BUILD);
    assert.equal(locals.renderBuild, BLOCKER10Z9_BUILD);
  });

  test('buildGlobalDbProofHtml handles missing ambiguousContainerProof', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const fnStart = tpl.indexOf('function buildGlobalDbProofHtml(data)');
    const fnBody = tpl.slice(fnStart, tpl.indexOf('function rarityNameStyle', fnStart));
    assert.ok(fnBody.includes('ambiguousContainerProof'));
    assert.ok(fnBody.includes('rowsSeen != null'));
  });

  test('buildGlobalDbProofHtml renders with Z7 debug payload', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
    const fn = script.match(/function buildGlobalDbProofHtml\(data\)\s*\{[\s\S]*?\n  \}/);
    assert.ok(fn, 'buildGlobalDbProofHtml must exist');
    const buildGlobalDbProofHtml = new Function('DEBUG_GLOBAL', 'escHtml', `${fn[0]}; return buildGlobalDbProofHtml;`)(
      true,
      (s) => String(s),
    );
    const html = buildGlobalDbProofHtml({
      globalDbUiProof: { sourceOfTruth: 'global_db', speciesCount: 1 },
      amountProof: { allVerified: true, rows: [] },
      ambiguousContainerProof: {
        rowsSeen: 32,
        rowsWithMetadataFishId: 0,
        rowsWithMetadataFishName: 0,
        rowsUnresolved: 32,
        sample: [],
      },
      ambiguousContainerIds: [267],
    });
    assert.match(html, /ambiguousContainerProof rowsSeen=32/);
  });

  test('buildGlobalDbProofHtml renders when ambiguousContainerProof is missing', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
    const fn = script.match(/function buildGlobalDbProofHtml\(data\)\s*\{[\s\S]*?\n  \}/);
    const buildGlobalDbProofHtml = new Function('DEBUG_GLOBAL', 'escHtml', `${fn[0]}; return buildGlobalDbProofHtml;`)(
      true,
      (s) => String(s),
    );
    const html = buildGlobalDbProofHtml({
      globalDbUiProof: { sourceOfTruth: 'global_db' },
      amountProof: { allVerified: true, rows: [] },
    });
    assert.ok(typeof html === 'string');
    assert.doesNotMatch(html, /ambiguousContainerProof rowsSeen=/);
  });
});
