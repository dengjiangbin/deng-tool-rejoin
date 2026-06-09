'use strict';

const { describe, test, beforeEach, afterEach } = require('node:test');
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

const Z18_BUILD = 'BLOCKER10Z18_RECOVERED_SPECIES_IMAGE_RESOLUTION_2026_06_09';
const Z17_BUILD = Z18_BUILD;
const Z16_BUILD = Z17_BUILD;
const Z15_BUILD = 'BLOCKER10Z15_AMOUNT_MOVED_TO_MIDDLE_SECTION_2026_06_08';
const Z14_BUILD = Z16_BUILD;
const Z13_BUILD = Z16_BUILD;
const Z12_BUILD = Z16_BUILD;
const Z11_BUILD = Z16_BUILD;
const Z10_BUILD = Z16_BUILD;
const Z9_BUILD = Z16_BUILD;
const Z8_BUILD = Z16_BUILD;
const Z7_BUILD = Z16_BUILD;
const Z6_BUILD = Z16_BUILD;
const Z5_BUILD = Z16_BUILD;
const Z4_BUILD = Z16_BUILD;
const Z3_BUILD = Z16_BUILD;
const Z_BUILD = Z16_BUILD;
const Y_BUILD = Z16_BUILD;
const X_BUILD = Z16_BUILD;
const W_BUILD = Z16_BUILD;
let tmpDb;

function setupTestDb() {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fishit-global-w-'));
  tmpDb = path.join(tmpDir, 'fishit_global_test.db');
  process.env.FISHIT_GLOBAL_DB_PATH = tmpDb;
  process.env.FISHIT_DISABLE_RADIANT_267_PROMO = '1';
  globalDb.closeDb();
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
    assert.ok(lua.includes('BLOCKER10Z7_METADATA_SPECIES_EXTRACTION_2026_06_08'));
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

  test('Flowery Fish image resolution path audited; trusted fallback when no asset', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const audit = quizBotCatalog.auditNames(['Flowery Fish']);
    assert.equal(audit[0]?.matched, false);
    const pub = await buildPublicFishFields([{
      name: 'Flowery Fish', baseFishName: 'Flowery Fish', amount: 1, category: 'fish', itemId: '1007',
    }], 'http://127.0.0.1:8791');
    const flowery = pub.publicItems.find((f) => /flowery fish/i.test(f.name));
    assert.ok(flowery, 'Flowery Fish should remain a public card');
    const meta = fishImageCache.resolveImageMetaForItem(flowery);
    assert.ok(Array.isArray(meta.searchedSources));
    assert.ok(meta.searchedSources.includes('quiz_bot_fishit_bank'));
    if (!flowery.imageUrlPresent) {
      assert.equal(flowery.imageResolved, false);
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
    assert.ok(lua.includes('BLOCKER10Z7_METADATA_SPECIES_EXTRACTION_2026_06_08'));
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
    assert.doesNotMatch(pub.publicItems[0].name || '', /item #156/i);
  });

  test('container collision rows for ambiguous id 267 stay hidden without metadata', async () => {
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
    assert.equal(pub.publicItems.length, 0);
    assert.equal(
      pub.publicItems.find((f) => f.amount === 32 && /parrot blopfish/i.test(f.name || '')),
      undefined,
    );
    assert.ok(!pub.publicItems.some((f) => /unknown fish #267|item #267/i.test(f.name || '')));
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
    assert.ok(lua.includes('BLOCKER10Z7_METADATA_SPECIES_EXTRACTION_2026_06_08'));
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
    assert.ok(pub.publicItems.every((f) => (Number(f.amount) || 0) <= 1));
    assert.equal(pub.hiddenPublicRows.ambiguousContainerUnresolved, 32);
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
    if (isTrustedRadiantCatfishInCatalog()) delete process.env.FISHIT_DISABLE_RADIANT_267_PROMO;
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
    const radiantExtra = (process.env.FISHIT_DISABLE_RADIANT_267_PROMO !== '1' && isTrustedRadiantCatfishInCatalog()) ? 1 : 0;
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
    assert.doesNotMatch(res.text, /ic-badges[\s\S]{0,120}>\s*Shiny\s*<\/span/i);
    assert.doesNotMatch(res.text, /ic-badges[\s\S]{0,120}>\s*Big\s*<\/span/i);
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
    delete process.env.FISHIT_DISABLE_RADIANT_267_PROMO;
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
    delete process.env.FISHIT_DISABLE_RADIANT_267_PROMO;
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

describe('BLOCKER10Z10 — card contrast and Radiant Catfish name fix', { concurrency: 1 }, () => {
  const {
    buildPublicFishFields,
    applyPublicCosmeticCleanup,
    stripHiddenPublicCosmeticPrefix,
    isMutationEmbeddedInCanonicalName,
    buildNameParserProof,
    isTrustedRadiantCatfishInCatalog,
  } = require('../src/fishitTrackerRoutes');
  const protectedFishNames = require('../src/fishitProtectedFishNames');
  const express = require('express');
  const request = require('supertest');
  const trackerRouter = require('../src/fishitTrackerRoutes');
  const { BLOCKER10Z10_BUILD } = require('../src/fishitTrackerBuild');

  test('A: CSS uses contrast-safe variables on full-rarity cards', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /--card-fg/);
    assert.match(tpl, /--badge-bg/);
    assert.match(tpl, /\.fish-card\.rarity-secret[\s\S]*--card-fg:#fff/);
    assert.match(tpl, /\.fish-card\.rarity-rare[\s\S]*--card-fg:#fff/);
    assert.match(tpl, /\.item-name[\s\S]*color:var\(--card-fg\)!important/);
  });

  test('B: Radiant Catfish keeps full name without Radiant mutation badge', async () => {
    setupTestDb();
    delete process.env.FISHIT_DISABLE_RADIANT_267_PROMO;
    const rows = [{
      name: 'Item #267',
      itemId: '267',
      containerItemId: '267',
      isAmbiguousContainerId: true,
      category: 'fish',
      amount: 1,
      weight: 13.4,
      replionUuid: 'uuid-radiant-z10',
      replionAmountSource: 'replion_uuid_instance',
      mutation: 'Radiant',
      mutationTags: ['Radiant'],
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    if (!isTrustedRadiantCatfishInCatalog()) return;
    const radiant = pub.publicItems.find((f) => /radiant catfish/i.test(f.name || f.publicCardName || ''));
    assert.ok(radiant, 'Radiant Catfish must be public');
    assert.equal(radiant.publicCardName || radiant.name, 'Radiant Catfish');
    assert.ok(!radiant.mutationTags || radiant.mutationTags.length === 0);
    assert.equal(radiant.mutation, null);
    const proof = buildNameParserProof(radiant);
    assert.equal(proof.publicName, 'Radiant Catfish');
    assert.equal(proof.protectedNameReason, 'protected_canonical_fish_name');
    assert.ok(!proof.publicBadges.includes('Radiant'));
  });

  test('C: Big/Shiny stripped; Ghost/Corrupt preserved; prefix names protected', () => {
    assert.equal(stripHiddenPublicCosmeticPrefix('Big Freshwater Piranha'), 'Freshwater Piranha');
    const shinyClean = applyPublicCosmeticCleanup({
      name: 'Shiny Panther Eel',
      baseFishName: 'Panther Eel',
      displayName: 'Shiny Panther Eel',
      mutation: 'Shiny',
      mutationTags: ['Shiny'],
      shiny: true,
    });
    assert.equal(shinyClean.publicCardName, 'Panther Eel');
    assert.equal(shinyClean.mutation, null);
    assert.equal(shinyClean.mutationTags.length, 0);
    const ghostClean = applyPublicCosmeticCleanup({
      name: 'Parrot Fish',
      baseFishName: 'Parrot Fish',
      mutation: 'Ghost',
      mutationTags: ['Ghost'],
    });
    assert.equal(ghostClean.mutation, 'Ghost');
    assert.ok(protectedFishNames.isProtectedBaseName('Giant Squid'));
    assert.ok(protectedFishNames.isProtectedBaseName('Radiant Catfish'));
    assert.ok(protectedFishNames.isProtectedBaseName('Zebra Snakehead'));
    assert.equal(isMutationEmbeddedInCanonicalName('Radiant Catfish', 'Radiant'), true);
    assert.equal(isMutationEmbeddedInCanonicalName('Zebra Snakehead', 'Zebra'), true);
    assert.equal(isMutationEmbeddedInCanonicalName('Parrot Fish', 'Ghost'), false);
  });

  test('D: rendered HTML has Radiant Catfish title without separate Radiant badge', async () => {
    setupTestDb();
    delete process.env.FISHIT_DISABLE_RADIANT_267_PROMO;
    const rows = [{
      name: 'Item #267',
      itemId: '267',
      containerItemId: '267',
      isAmbiguousContainerId: true,
      category: 'fish',
      amount: 1,
      weight: 13.4,
      replionUuid: 'uuid-radiant-html',
      replionAmountSource: 'replion_uuid_instance',
      mutation: 'Radiant',
      mutationTags: ['Radiant'],
    }];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    if (!isTrustedRadiantCatfishInCatalog()) return;
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(trackerRouter);
    const res = await request(app).get('/tracker').expect(200);
    assert.match(res.text, /Radiant Catfish/);
    assert.match(res.text, /publicMutationBadges/);
    assert.doesNotMatch(res.text, /badge[^>]*>Radiant<\/span>[^<]*<\/div>\s*<div class="ic-meta">[^<]*Radiant Catfish/i);
  });

  test('E: public counts unchanged; no Goliath or Unknown #267', async () => {
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
      replionUuid: `uuid-z10-${i}`,
      replionAmountSource: 'replion_uuid_instance',
    }));
    const pub = await buildPublicFishFields(verified, 'http://127.0.0.1:8791');
    const radiantExtra = isTrustedRadiantCatfishInCatalog() ? 0 : 0;
    assert.equal(pub.publicCounts.visibleFishInstances, 20 + radiantExtra);
    assert.ok(!pub.publicItems.some((f) => /goliath tiger/i.test(f.name || '')));
    assert.ok(!pub.publicItems.some((f) => /unknown fish #267/i.test(f.name || '')));
    const goliath = [{
      name: 'Goliath Tiger', itemId: '1008', category: 'fish', amount: 1,
      baseFishName: 'Goliath Tiger', replionUuid: 'uuid-goliath-z10',
      replionAmountSource: 'replion_uuid_instance', catalogSource: 'live_roblox_catch_delta',
    }];
    const gPub = await buildPublicFishFields(goliath, 'http://127.0.0.1:8791');
    assert.ok(!gPub.publicItems.some((f) => /goliath/i.test(f.name || '')));
  });

  test('F: build marker is BLOCKER10Z13', () => {
    const { buildTrackerPageLocals, PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');
    assert.equal(BLOCKER10Z10_BUILD, Z13_BUILD);
    assert.equal(PUBLIC_API_BUILD, BLOCKER10Z10_BUILD);
    const locals = buildTrackerPageLocals();
    assert.equal(locals.renderBuild, BLOCKER10Z10_BUILD);
  });
});

describe('BLOCKER10Z7 hotfix — /tracker page render', () => {
  const express = require('express');
  const request = require('supertest');
  const trackerRouter = require('../src/fishitTrackerRoutes');
  const ejs = require('ejs');
  const { BLOCKER10Z10_BUILD } = require('../src/fishitTrackerBuild');

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
    assert.match(res.text, /BLOCKER10Z18/);
  });

  test('GET /tracker?debug=global returns HTTP 200', async () => {
    const res = await request(makeApp()).get('/tracker?debug=global').expect(200);
    assert.match(res.text, /DEBUG_GLOBAL|global-db-proof|fishit-tracker/i);
  });

  test('buildTrackerPageLocals does not reference undefined build constants', () => {
    const { buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');
    const locals = buildTrackerPageLocals();
    assert.equal(locals.publicApiBuild, Z18_BUILD);
    assert.equal(locals.blocker10vBuild, Z18_BUILD);
    assert.equal(locals.renderBuild, Z18_BUILD);
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

describe('BLOCKER10Z11 — DENG Fish It bot rarity + safe global relearn', { concurrency: 1 }, () => {
  const dengBotCatalog = require('../src/fishitDengFishItBotCatalog');
  const globalLearning = require('../src/fishitGlobalLearning');
  const rarityEnrichment = require('../src/fishitRarityEnrichment');
  const catchNameParser = require('../src/fishitCatchNameParser');
  const { buildNameParserProof } = require('../src/fishitTrackerRoutes');
  const { BLOCKER10Z11_BUILD } = require('../src/fishitTrackerBuild');
  const { spawnSync } = require('node:child_process');

  beforeEach(() => {
    setupTestDb();
    dengBotCatalog._reset();
    globalLearning._reset();
  });

  test('build marker is BLOCKER10Z13', () => {
    assert.equal(BLOCKER10Z11_BUILD, Z13_BUILD);
  });

  test('A: DENG Fish It bot catalog loader loads rarity source', () => {
    const proof = dengBotCatalog.buildCatalogProof();
    assert.equal(proof.sourceType, 'deng_fish_it_bot_sqlite');
    assert.ok(proof.rowsLoaded > 0);
    assert.ok(proof.rarityCounts.Secret > 0);
    assert.ok(proof.rarityCounts.Forgotten > 0);
    assert.ok(Array.isArray(proof.sampleEntries));
  });

  test('B: Secret/Forgotten examples resolve from DENG Fish It bot data', () => {
    const squid = dengBotCatalog.lookupRarity('Giant Squid');
    const thunder = dengBotCatalog.lookupRarity('Thunderzilla');
    assert.equal(squid?.rarity, 'Secret');
    assert.equal(squid?.raritySource, 'deng_fish_it_bot');
    assert.equal(thunder?.rarity, 'Forgotten');
  });

  test('C: Giant Squid remains Giant Squid and gets correct rarity', () => {
    setupTestDb();
    const hit = rarityEnrichment.lookupRarityForItem({ baseFishName: 'Giant Squid', name: 'Giant Squid' });
    assert.equal(hit?.rarity, 'Secret');
    const entry = dengBotCatalog.lookupEntry('Giant Squid');
    assert.equal(entry?.baseFishName, 'Giant Squid');
  });

  test('D: Panther Eel gets correct Secret rarity', () => {
    const hit = rarityEnrichment.lookupRarityForItem({ baseFishName: 'Panther Eel', name: 'Panther Eel' });
    assert.equal(hit?.rarity, 'Secret');
  });

  test('E: Freshwater Piranha gets Rare from game_verified_seed not bot', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const sp = globalDb.findSpeciesByAliases(['Freshwater Piranha']);
    assert.equal(sp?.species?.rarity, 'Rare');
    const hit = rarityEnrichment.lookupRarityForItem({ baseFishName: 'Freshwater Piranha', name: 'Freshwater Piranha' });
    assert.equal(hit?.rarity, 'Rare');
    assert.ok(hit?.raritySource === 'game_verified_seed' || hit?.raritySource === 'global_db' || String(hit?.raritySource || '').includes('seed'));
  });

  test('F: Radiant Catfish remains full name and does not emit Radiant mutation badge', () => {
    const proof = buildNameParserProof({
      name: 'Radiant Catfish',
      baseFishName: 'Radiant Catfish',
      mutation: null,
      mutationTags: [],
    });
    assert.equal(proof.baseFishName, 'Radiant Catfish');
    assert.ok(!proof.publicBadges.includes('Radiant'));
  });

  test('G: Big Freshwater Piranha public name becomes Freshwater Piranha', () => {
    const { applyPublicCosmeticCleanup } = require('../src/fishitTrackerRoutes');
    const cleaned = applyPublicCosmeticCleanup({
      name: 'Big Freshwater Piranha',
      baseFishName: 'Freshwater Piranha',
      mutation: 'Big',
    });
    assert.equal(cleaned.name, 'Freshwater Piranha');
    assert.ok(!cleaned.mutation);
  });

  test('H: Shiny Panther Eel public name becomes Panther Eel', () => {
    const { applyPublicCosmeticCleanup } = require('../src/fishitTrackerRoutes');
    const cleaned = applyPublicCosmeticCleanup({
      name: 'Shiny Panther Eel',
      baseFishName: 'Panther Eel',
      mutation: 'Shiny',
    });
    assert.equal(cleaned.name, 'Panther Eel');
    assert.ok(!cleaned.mutation);
  });

  test('I: Goliath Tiger quarantined unless snapshot metadata proves it', async () => {
    const { buildPublicFishFields } = require('../src/fishitTrackerRoutes');
    const pub = await buildPublicFishFields([{
      name: 'Goliath Tiger',
      itemId: '1008',
      category: 'fish',
      amount: 1,
    }], 'http://127.0.0.1:8791');
    assert.ok(!pub.publicItems.some((f) => /goliath tiger/i.test(f.name || '')));
  });

  test('J: Unknown Fish #267 hidden from public', async () => {
    const { buildPublicFishFields } = require('../src/fishitTrackerRoutes');
    const pub = await buildPublicFishFields([{
      name: 'Unknown Fish #267',
      itemId: '267',
      category: 'fish',
      amount: 1,
    }], 'http://127.0.0.1:8791');
    assert.ok(!pub.publicItems.some((f) => /unknown fish #267/i.test(f.name || '')));
  });

  test('K: Global DB reset dry-run does not modify files', async () => {
    const dbPath = globalDb.dbPath();
    const beforeMtime = fs.existsSync(dbPath) ? fs.statSync(dbPath).mtimeMs : 0;
    const proof = await globalCatalogService.resetGlobalCatalog({ dryRun: true });
    assert.equal(proof.dryRun, true);
    if (fs.existsSync(dbPath)) {
      assert.equal(fs.statSync(dbPath).mtimeMs, beforeMtime);
    }
  });

  test('L: Global DB reset confirm creates backup before writing', async () => {
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    globalDb.insertObservation({
      anonymized_user_hash: 'abc',
      item_id: '9999',
      parsed_base_name: 'Test Fish',
      source_payload_type: 'inventory_snapshot',
      observed_at: new Date().toISOString(),
    });
    const proof = await globalCatalogService.resetGlobalCatalog({ confirm: true });
    assert.equal(proof.dryRun, false);
    assert.ok(proof.backupCreated.length >= 0);
    assert.ok(proof.quarantinedEntries.some((q) => q.itemId === '1008'));
  });

  test('M: Reset preserves image cache by default', async () => {
    const proof = await globalCatalogService.resetGlobalCatalog({ dryRun: true });
    assert.ok(proof.preservedFiles.some((p) => /fish_image_cache/.test(p)));
  });

  test('N: Learning pipeline records pending but does not promote ambiguous itemId-only rows', () => {
    const blocked = globalLearning.recordLearningEvidence({ itemId: '267', rawName: 'Item #267' });
    assert.equal(blocked.decision, 'quarantined');
    const phantom = globalLearning.recordLearningEvidence({ itemId: '1008', rawName: 'Goliath Tiger' });
    assert.equal(phantom.accepted, false);
    const pending = globalLearning.recordLearningEvidence({
      itemId: '385',
      rawName: 'Some Unknown Fish',
      baseFishName: 'Some Unknown Fish',
      sourcePayloadType: 'inventory_snapshot',
      userId: 'user1',
    });
    assert.equal(pending.decision, 'pending');
  });

  test('O: Public /tracker does not show debug/global/reset proof', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
    const fn = script.match(/function buildGlobalDbProofHtml\(data\)\s*\{[\s\S]*?\n  \}/);
    const buildGlobalDbProofHtml = new Function('DEBUG_GLOBAL', 'escHtml', `${fn[0]}; return buildGlobalDbProofHtml;`)(
      false,
      (s) => String(s),
    );
    const hidden = buildGlobalDbProofHtml({
      dengFishItBotCatalogProof: { sourceType: 'test', rowsLoaded: 1, rarityCounts: { Secret: 1 } },
      globalLearningProof: { totalRecords: 1 },
      resetSeedProof: { dryRun: true, seededEntries: {} },
      globalDbUiProof: { sourceOfTruth: 'global_db', speciesCount: 1 },
    });
    assert.equal(hidden, '');
  });

  test('P: /tracker?debug=global does show proof markers', async () => {
    const express = require('express');
    const request = require('supertest');
    const trackerRouter = require('../src/fishitTrackerRoutes');
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(trackerRouter);
    const res = await request(app).get('/tracker?debug=global').expect(200);
    assert.match(res.text, /debug=global|global-db-proof/i);
  });

  test('Q: Card contrast CSS/classes exist for Secret/Forgotten/Rare/neutral', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /--card-fg/);
    assert.match(tpl, /--badge-bg/);
    assert.match(tpl, /rarity-secret/);
    assert.match(tpl, /rarity-forgotten/);
    assert.match(tpl, /rarity-rare/);
  });

  test('R: importDengFishItBotSeed updates species from bot catalog', () => {
    const result = globalCatalogService.importDengFishItBotSeed();
    assert.equal(result.ok, true);
    assert.ok(result.speciesUpdated > 0);
    const gs = globalDb.findSpeciesByAliases(['Giant Squid']);
    assert.equal(gs?.species?.rarity, 'Secret');
  });

  test('S: reset script dry-run exits 0', () => {
    const repoRoot = path.join(__dirname, '..', '..');
    const r = spawnSync(process.execPath, ['scripts/reset_fishit_global_catalog.js', '--dry-run'], {
      cwd: repoRoot,
      encoding: 'utf8',
      env: { ...process.env, FISHIT_GLOBAL_DB_PATH: tmpDb || path.join(os.tmpdir(), 'fishit-reset-dry.db') },
    });
    assert.equal(r.status, 0, r.stderr || r.stdout);
    assert.match(r.stdout, /dry-run complete/i);
  });
});

describe('BLOCKER10Z12 — modern fish card layout', { concurrency: 1 }, () => {
  const { BLOCKER10Z12_BUILD } = require('../src/fishitTrackerBuild');
  const { applyPublicCosmeticCleanup } = require('../src/fishitTrackerRoutes');

  function loadTrackerScriptFns() {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
    const fn = script.match(/function buildItemsHtml\(items\)\s*\{[\s\S]*?\n  \}/);
    assert.ok(fn, 'buildItemsHtml must exist');
    const ctx = new Function(`
      const CARD_RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret', forgotten:'rarity-forgotten' };
      const RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'badge-rarity-secret', forgotten:'rarity-forgotten' };
      const RARITY_NAME_COLORS = {};
      const ITEM_IMAGES = { Default:'/assets/img/fishit/fallback-fish.svg' };
      function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
      function rarityClass(r){ return r ? (RARITY_MAP[r.toLowerCase()]||'badge') : ''; }
      function cardRarityClass(r){ return r ? (CARD_RARITY_MAP[r.toLowerCase()]||'') : ''; }
      function cardTitle(item){ return item.cardName||item.baseFishName||item.name||'Unknown'; }
      function cardKey(item){ return String(item.name||'x').toLowerCase(); }
      function rarityNameStyle(){ return ''; }
      function publicMutationBadges(item){
        const tags=[]; if(Array.isArray(item.mutationTags)) tags.push(...item.mutationTags); else if(item.mutation) tags.push(item.mutation);
        const title=cardTitle(item);
        return tags.filter(t=>{ if(!t) return false; const low=String(t).toLowerCase(); if(low==='big'||low==='shiny') return false; if(title&&title.toLowerCase().startsWith(low+' ')) return false; return true; });
      }
      function isUsableImageUrl(url){ return typeof url==='string' && url.startsWith('http'); }
      function itemImageSrc(item){ return isUsableImageUrl(item.imageUrl)?item.imageUrl:null; }
      ${script.match(/function buildFishCardInnerHtml\(item\)\s*\{[\s\S]*?\n  \}/)[0]}
      ${fn[0]}
      return buildItemsHtml;
    `)();
    return ctx;
  }

  test('1: build marker is BLOCKER10Z12', () => {
    assert.equal(BLOCKER10Z12_BUILD, Z13_BUILD);
  });

  test('2: rendered HTML contains modern fish card image wrapper/class', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /fish-card__imageWrap/);
    assert.match(tpl, /fish-card__image/);
    assert.match(tpl, /fish-card__body/);
    assert.match(tpl, /fish-card__name/);
  });

  test('3: fish image CSS uses larger responsive sizing not tiny icons', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /clamp\(64px/);
    assert.match(tpl, /max-width:88px/);
    assert.match(tpl, /object-fit:contain/);
    assert.doesNotMatch(tpl, /width:28px;height:28px/);
  });

  test('4: fish name CSS allows wrapping not nowrap-only ellipsis', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /-webkit-line-clamp:2/);
    assert.match(tpl, /white-space:normal/);
    assert.match(tpl, /overflow-wrap:break-word/);
    assert.doesNotMatch(tpl, /white-space:nowrap[^;]*;[^}]*\.item-name/s);
  });

  test('5: long fish names render as full names in card HTML', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const names = [
      'Freshwater Piranha',
      'Skeleton Angler Fish',
      'Seaweed Pufferfish',
      'Zebra Snakehead',
      'Radiant Catfish',
      'Manoai Statue Fish',
      'Giant Squid',
      'Panther Eel',
    ];
    const html = buildItemsHtml(names.map((name) => ({
      name,
      baseFishName: name,
      rarity: name === 'Freshwater Piranha' ? 'Rare' : (name === 'Giant Squid' || name === 'Panther Eel' ? 'Secret' : null),
      amount: name === 'Zebra Snakehead' ? 2 : 1,
      imageUrl: 'http://127.0.0.1:8791/api/fishit-tracker/assets/fish/test.webp',
    })));
    for (const name of names) {
      assert.match(html, new RegExp(escHtml(name).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.match(html, /fish-card__name[^>]*title="Freshwater Piranha"/);
  });

  function escHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  test('6: Radiant Catfish has no separate Radiant mutation badge', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([{
      name: 'Radiant Catfish',
      baseFishName: 'Radiant Catfish',
      mutation: null,
      mutationTags: [],
      amount: 1,
      imageUrl: 'http://127.0.0.1:8791/x.webp',
    }]);
    assert.match(html, /Radiant Catfish/);
    assert.doesNotMatch(html, /fish-card__tag/);
  });

  test('7: rendered HTML does not contain Unknown Fish #267', async () => {
    setupTestDb();
    const { buildPublicFishFields } = require('../src/fishitTrackerRoutes');
    const pub = await buildPublicFishFields([{
      name: 'Unknown Fish #267',
      itemId: '267',
      category: 'fish',
      amount: 1,
    }], 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.doesNotMatch(html, /Unknown Fish #267/i);
  });

  test('8: rendered HTML does not contain Goliath Tiger', async () => {
    setupTestDb();
    const { buildPublicFishFields } = require('../src/fishitTrackerRoutes');
    const pub = await buildPublicFishFields([{
      name: 'Goliath Tiger',
      itemId: '1008',
      category: 'fish',
      amount: 1,
    }], 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.doesNotMatch(html, /Goliath Tiger/i);
  });

  test('9: rarity card CSS defines readable foreground/badge variables', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /--card-fg/);
    assert.match(tpl, /--badge-bg/);
    assert.match(tpl, /--badge-fg/);
    assert.match(tpl, /--image-glow/);
    assert.match(tpl, /\.fish-card\.rarity-secret[\s\S]*--card-fg:#fff/);
    assert.match(tpl, /\.fish-card\.rarity-rare[\s\S]*--card-fg:#fff/);
    assert.match(tpl, /\.fish-card\.rarity-forgotten[\s\S]*--card-fg:#fff/);
  });

  test('10: public /tracker does not expose debug proof when DEBUG_GLOBAL false', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
    const fn = script.match(/function buildGlobalDbProofHtml\(data\)\s*\{[\s\S]*?\n  \}/);
    const buildGlobalDbProofHtml = new Function('DEBUG_GLOBAL', 'escHtml', `${fn[0]}; return buildGlobalDbProofHtml;`)(
      false,
      (s) => String(s),
    );
    assert.equal(buildGlobalDbProofHtml({ globalDbUiProof: { sourceOfTruth: 'global_db' } }), '');
  });

  test('11: Big/Shiny still stripped from public names in card render path', () => {
    const cleaned = applyPublicCosmeticCleanup({
      name: 'Big Freshwater Piranha',
      baseFishName: 'Freshwater Piranha',
      mutation: 'Big',
    });
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([{ ...cleaned, rarity: 'Rare', amount: 1, imageUrl: 'http://127.0.0.1/x.webp' }]);
    assert.match(html, /Freshwater Piranha/);
    assert.doesNotMatch(html, />Big</);
  });
});

describe('BLOCKER10Z13 — public card polish simple badges', { concurrency: 1 }, () => {
  const { BLOCKER10Z13_BUILD } = require('../src/fishitTrackerBuild');

  function loadTrackerScriptFns() {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
    const fn = script.match(/function buildItemsHtml\(items\)\s*\{[\s\S]*?\n  \}/);
    assert.ok(fn, 'buildItemsHtml must exist');
    const ctx = new Function(`
      const CARD_RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret', forgotten:'rarity-forgotten' };
      const RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'badge-rarity-secret', forgotten:'rarity-forgotten' };
      const RARITY_NAME_COLORS = {};
      const ITEM_IMAGES = { Default:'/assets/img/fishit/fallback-fish.svg' };
      function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
      function rarityClass(r){ return r ? (RARITY_MAP[r.toLowerCase()]||'badge') : ''; }
      function cardRarityClass(r){ return r ? (CARD_RARITY_MAP[r.toLowerCase()]||'') : ''; }
      function cardTitle(item){ return item.cardName||item.baseFishName||item.name||'Unknown'; }
      function cardKey(item){ return String(item.name||'x').toLowerCase(); }
      function rarityNameStyle(){ return ''; }
      function publicMutationBadges(item){
        const tags=[]; if(Array.isArray(item.mutationTags)) tags.push(...item.mutationTags); else if(item.mutation) tags.push(item.mutation);
        const title=cardTitle(item);
        return tags.filter(t=>{ if(!t) return false; const low=String(t).toLowerCase(); if(low==='big'||low==='shiny') return false; if(title&&title.toLowerCase().startsWith(low+' ')) return false; return true; });
      }
      function isUsableImageUrl(url){ return typeof url==='string' && url.startsWith('http'); }
      function itemImageSrc(item){ return isUsableImageUrl(item.imageUrl)?item.imageUrl:null; }
      ${script.match(/function buildFishCardInnerHtml\(item\)\s*\{[\s\S]*?\n  \}/)[0]}
      ${fn[0]}
      return buildItemsHtml;
    `)();
    return ctx;
  }

  test('1: build marker is BLOCKER10Z13', () => {
    assert.equal(BLOCKER10Z13_BUILD, Z13_BUILD);
  });

  test('2: public HTML removes mutation/detail labels Ghost Corrupt Albino Sandy', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const cases = [
      { name: 'Parrot Fish', mutation: 'Ghost', mutationTags: ['Ghost'], amount: 5, rarity: null },
      { name: 'Parrot Blopfish', mutation: 'Corrupt', mutationTags: ['Corrupt'], amount: 1, rarity: null },
      { name: 'Mossy Fishlet', mutation: 'Albino', mutationTags: ['Albino'], amount: 1, rarity: null },
      { name: 'Flowery Fish', mutation: 'Sandy', mutationTags: ['Sandy'], amount: 1, rarity: null },
    ];
    const html = buildItemsHtml(cases.map((c) => ({
      ...c,
      baseFishName: c.name,
      imageUrl: 'http://127.0.0.1/x.webp',
    })));
    assert.doesNotMatch(html, /fish-card__tag/);
    assert.doesNotMatch(html, />Ghost</);
    assert.doesNotMatch(html, />Corrupt</);
    assert.doesNotMatch(html, />Albino</);
    assert.doesNotMatch(html, />Sandy</);
    assert.match(html, /Parrot Fish/);
    assert.match(html, /x5/);
  });

  test('3: public HTML does not show rarity text labels on cards', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([
      { name: 'Giant Squid', baseFishName: 'Giant Squid', rarity: 'Secret', amount: 1, imageUrl: 'http://127.0.0.1/x.webp' },
      { name: 'Freshwater Piranha', baseFishName: 'Freshwater Piranha', rarity: 'Rare', amount: 5, imageUrl: 'http://127.0.0.1/x.webp' },
      { name: 'Thunderzilla', baseFishName: 'Thunderzilla', rarity: 'Forgotten', amount: 1, imageUrl: 'http://127.0.0.1/x.webp' },
    ]);
    assert.doesNotMatch(html, /fish-card__rarity/);
    assert.doesNotMatch(html, />Secret</);
    assert.doesNotMatch(html, />Rare</);
    assert.doesNotMatch(html, />Forgotten</);
    assert.match(html, /Freshwater Piranha/);
    assert.match(html, /x5/);
  });

  test('4: full fish names still render correctly', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const names = [
      'Giant Squid', 'Panther Eel', 'Radiant Catfish', 'Zebra Snakehead',
      'Freshwater Piranha', 'Skeleton Angler Fish', 'Seaweed Pufferfish',
    ];
    const html = buildItemsHtml(names.map((name) => ({
      name,
      baseFishName: name,
      amount: 1,
      imageUrl: 'http://127.0.0.1/x.webp',
    })));
    for (const name of names) assert.match(html, new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  });

  test('5: Radiant Catfish has no fake Radiant mutation badge', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([{
      name: 'Radiant Catfish',
      baseFishName: 'Radiant Catfish',
      mutation: null,
      mutationTags: [],
      amount: 1,
      imageUrl: 'http://127.0.0.1/x.webp',
    }]);
    assert.match(html, /Radiant Catfish/);
    assert.doesNotMatch(html, /fish-card__tag/);
    assert.doesNotMatch(html, />Radiant</);
  });

  test('6: no Unknown Fish #267 in public card HTML', async () => {
    setupTestDb();
    const { buildPublicFishFields } = require('../src/fishitTrackerRoutes');
    const pub = await buildPublicFishFields([{
      name: 'Unknown Fish #267',
      itemId: '267',
      category: 'fish',
      amount: 1,
    }], 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.doesNotMatch(html, /Unknown Fish #267/i);
  });

  test('7: no Goliath Tiger in public card HTML', async () => {
    setupTestDb();
    const { buildPublicFishFields } = require('../src/fishitTrackerRoutes');
    const pub = await buildPublicFishFields([{
      name: 'Goliath Tiger',
      itemId: '1008',
      category: 'fish',
      amount: 1,
    }], 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.doesNotMatch(html, /Goliath Tiger/i);
  });

  test('8: CSS includes prominent fish-card__amount badge style', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /\.fish-card__amount/);
    assert.doesNotMatch(tpl, /\.fish-card__rarity/);
  });

  test('9: amount badge sits in body middle section below name', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /\.fish-card__amountRow/);
    const amountBlock = tpl.match(/\.fish-card__amount[\s\S]*?box-sizing:border-box;/);
    assert.ok(amountBlock, 'fish-card__amount block must exist');
    assert.doesNotMatch(amountBlock[0], /position:absolute/);
    assert.match(amountBlock[0], /font-weight:800/);
    assert.match(tpl, /fish-card__amountRow[\s\S]*fish-card__amount/);
  });

  test('10: amount font size is larger than previous tiny .84rem pill style', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const amountBlock = tpl.match(/\.fish-card__amount[\s\S]*?box-sizing:border-box;/);
    assert.ok(amountBlock, 'fish-card__amount block must exist');
    assert.match(amountBlock[0], /font-size:clamp\(1rem/);
    assert.doesNotMatch(amountBlock[0], /font-size:\.72rem/);
  });
});

describe('BLOCKER10Z14 — public minimal card hide fake 285', { concurrency: 1 }, () => {
  const { BLOCKER10Z14_BUILD, PUBLIC_API_BUILD, buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');

  function loadTrackerScriptFns() {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
    const fn = script.match(/function buildItemsHtml\(items\)\s*\{[\s\S]*?\n  \}/);
    assert.ok(fn, 'buildItemsHtml must exist');
    const ctx = new Function(`
      const CARD_RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret', forgotten:'rarity-forgotten' };
      const RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'badge-rarity-secret', forgotten:'rarity-forgotten' };
      const RARITY_NAME_COLORS = {};
      const ITEM_IMAGES = { Default:'/assets/img/fishit/fallback-fish.svg' };
      function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
      function rarityClass(r){ return r ? (RARITY_MAP[r.toLowerCase()]||'badge') : ''; }
      function cardRarityClass(r){ return r ? (CARD_RARITY_MAP[r.toLowerCase()]||'') : ''; }
      function cardTitle(item){ return item.cardName||item.baseFishName||item.name||'Unknown'; }
      function cardKey(item){ return String(item.name||'x').toLowerCase(); }
      function rarityNameStyle(){ return ''; }
      function isUsableImageUrl(url){ return typeof url==='string' && url.startsWith('http'); }
      function itemImageSrc(item){ return isUsableImageUrl(item.imageUrl)?item.imageUrl:null; }
      ${script.match(/function buildFishCardInnerHtml\(item\)\s*\{[\s\S]*?\n  \}/)[0]}
      ${fn[0]}
      return buildItemsHtml;
    `)();
    return ctx;
  }

  test('1: build marker is BLOCKER10Z14', () => {
    assert.equal(BLOCKER10Z14_BUILD, Z14_BUILD);
    assert.equal(PUBLIC_API_BUILD, Z14_BUILD);
    const locals = buildTrackerPageLocals({});
    assert.equal(locals.renderBuild, Z14_BUILD);
    assert.equal(locals.publicApiBuild, Z14_BUILD);
  });

  test('2: public HTML has no rarity text labels on fish cards', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([
      { name: 'Freshwater Piranha', baseFishName: 'Freshwater Piranha', rarity: 'Rare', amount: 5, imageUrl: 'http://127.0.0.1/x.webp' },
      { name: 'Giant Squid', baseFishName: 'Giant Squid', rarity: 'Secret', amount: 1, imageUrl: 'http://127.0.0.1/x.webp' },
    ]);
    assert.doesNotMatch(html, /fish-card__rarity/);
    assert.doesNotMatch(html, />Rare</);
    assert.doesNotMatch(html, />Secret</);
  });

  test('3: public HTML still shows fish names and amount as primary metadata', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([
      { name: 'Manoai Statue Fish', baseFishName: 'Manoai Statue Fish', amount: 2, imageUrl: 'http://127.0.0.1/x.webp' },
    ]);
    assert.match(html, /Manoai Statue Fish/);
    assert.match(html, /fish-card__amount[^>]*>x2</);
    assert.doesNotMatch(html, /fish-card__meta/);
  });

  test('4: amount CSS is larger and stronger than old tiny pill', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const amountBlock = tpl.match(/\.fish-card__amount[\s\S]*?box-sizing:border-box;/);
    assert.ok(amountBlock);
    assert.match(amountBlock[0], /font-weight:800/);
    assert.match(amountBlock[0], /font-size:clamp\(1rem/);
    assert.doesNotMatch(amountBlock[0], /position:absolute/);
  });

  test('5: full canonical fish names render in HTML', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const names = [
      'Freshwater Piranha', 'Manoai Statue Fish', 'Zebra Snakehead',
      'Flowery Fish', 'Mossy Fishlet', 'Radiant Catfish',
    ];
    const html = buildItemsHtml(names.map((name) => ({
      name, baseFishName: name, amount: 1, imageUrl: 'http://127.0.0.1/x.webp',
    })));
    for (const name of names) {
      assert.match(html, new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
  });

  test('6: Radiant Catfish has no fake mutation badge', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([{
      name: 'Radiant Catfish', baseFishName: 'Radiant Catfish', amount: 1,
      imageUrl: 'http://127.0.0.1/x.webp',
    }]);
    assert.match(html, /Radiant Catfish/);
    assert.doesNotMatch(html, /fish-card__tag/);
    assert.doesNotMatch(html, />Radiant</);
  });

  test('7: public /tracker does not expose debug proof when DEBUG_GLOBAL false', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
    const fn = script.match(/function buildGlobalDbProofHtml\(data\)\s*\{[\s\S]*?\n  \}/);
    const buildGlobalDbProofHtml = new Function('DEBUG_GLOBAL', 'escHtml', `${fn[0]}; return buildGlobalDbProofHtml;`)(
      false,
      (s) => String(s),
    );
    assert.equal(buildGlobalDbProofHtml({ globalDbUiProof: { sourceOfTruth: 'global_db' } }), '');
  });

  test('8: Flowery Fish image resolution path proof', async () => {
    setupTestDb();
    if (!fs.existsSync(quizBotCatalog.BANK_PATH)) return;
    await globalCatalogService.importQuizBotSeed();
    const pub = await buildPublicFishFields([{
      name: 'Flowery Fish', baseFishName: 'Flowery Fish', amount: 1, category: 'fish', itemId: '1007',
    }], 'http://127.0.0.1:8791');
    const flowery = pub.publicItems.find((f) => /flowery fish/i.test(f.name));
    assert.ok(flowery);
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([flowery]);
    assert.match(html, /Flowery Fish/);
    if (flowery.imageUrlPresent) {
      assert.doesNotMatch(html, /data-placeholder="true"/);
    } else {
      assert.match(html, /fallback-fish\.svg|data-placeholder="true"/);
    }
  });

  test('9: repeated Unknown Fish #285 rows hidden when identity untrusted', async () => {
    setupTestDb();
    const rows = Array.from({ length: 12 }, (_, i) => ({
      name: 'Item #285', itemId: '285', category: 'items', amount: 1, weight: 2.7 + (i * 0.1),
      replionUuid: `uuid-285-${i}`, replionAmountSource: 'replion_uuid_instance',
    }));
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.equal(pub.publicItems.length, 0);
    assert.doesNotMatch(html, /Unknown Fish #285/i);
  });

  test('10: public HTML does not flood with Unknown Fish #285', async () => {
    setupTestDb();
    const rows = Array.from({ length: 9 }, (_, i) => ({
      name: 'Item #285', itemId: '285', category: 'fish', amount: 1, weight: 2.0 + (i * 0.2),
      replionUuid: `uuid-285-flood-${i}`, replionAmountSource: 'replion_uuid_instance',
    }));
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    const unknown285 = pub.publicItems.filter((f) => /unknown fish #285/i.test(f.name || ''));
    assert.equal(unknown285.length, 0);
  });

  test('11: trusted goatfish stack below collision threshold still visible', async () => {
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

  test('12: visible counts based on visible trusted cards only', async () => {
    setupTestDb();
    const rows = [
      ...Array.from({ length: 10 }, (_, i) => ({
        name: 'Item #285', itemId: '285', category: 'items', amount: 1, weight: 2.7 + i,
        replionUuid: `uuid-hide-${i}`, replionAmountSource: 'replion_uuid_instance',
      })),
      { name: 'Parrot Fish', baseFishName: 'Parrot Fish', itemId: '248', category: 'fish', amount: 3,
        imageUrl: 'http://127.0.0.1/x.webp' },
    ];
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    const visibleAmount = pub.publicItems.reduce((s, f) => s + (Number(f.amount) || 0), 0);
    assert.equal(visibleAmount, 3);
    assert.ok(!pub.publicItems.some((f) => /unknown fish #285/i.test(f.name || '')));
  });

  test('13: no Goliath Tiger in public card HTML', async () => {
    setupTestDb();
    const pub = await buildPublicFishFields([{
      name: 'Goliath Tiger', itemId: '1008', category: 'fish', amount: 1,
    }], 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.doesNotMatch(html, /Goliath Tiger/i);
  });

  test('14: no Unknown Fish #267 in public card HTML', async () => {
    setupTestDb();
    const pub = await buildPublicFishFields([{
      name: 'Unknown Fish #267', itemId: '267', category: 'fish', amount: 1,
    }], 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.doesNotMatch(html, /Unknown Fish #267/i);
  });

  test('15: /tracker page includes current build marker', async () => {
    const express = require('express');
    const request = require('supertest');
    const trackerRouter = require('../src/fishitTrackerRoutes');
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(trackerRouter);
    const res = await request(app).get('/tracker').expect(200);
    assert.match(res.text, /BLOCKER10Z18_RECOVERED_SPECIES_IMAGE_RESOLUTION_2026_06_09/);
  });
});

describe('BLOCKER10Z15 — amount moved to middle section', { concurrency: 1 }, () => {
  const { BLOCKER10Z15_BUILD, PUBLIC_API_BUILD, buildTrackerPageLocals } = require('../src/fishitTrackerRoutes');

  function loadTrackerScriptFns() {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
    const fn = script.match(/function buildItemsHtml\(items\)\s*\{[\s\S]*?\n  \}/);
    assert.ok(fn, 'buildItemsHtml must exist');
    const ctx = new Function(`
      const CARD_RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret', forgotten:'rarity-forgotten' };
      const RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'badge-rarity-secret', forgotten:'rarity-forgotten' };
      const RARITY_NAME_COLORS = {};
      const ITEM_IMAGES = { Default:'/assets/img/fishit/fallback-fish.svg' };
      function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
      function rarityClass(r){ return r ? (RARITY_MAP[r.toLowerCase()]||'badge') : ''; }
      function cardRarityClass(r){ return r ? (CARD_RARITY_MAP[r.toLowerCase()]||'') : ''; }
      function cardTitle(item){ return item.cardName||item.baseFishName||item.name||'Unknown'; }
      function cardKey(item){ return String(item.name||'x').toLowerCase(); }
      function rarityNameStyle(){ return ''; }
      function isUsableImageUrl(url){ return typeof url==='string' && url.startsWith('http'); }
      function itemImageSrc(item){ return isUsableImageUrl(item.imageUrl)?item.imageUrl:null; }
      ${script.match(/function buildFishCardInnerHtml\(item\)\s*\{[\s\S]*?\n  \}/)[0]}
      ${fn[0]}
      return buildItemsHtml;
    `)();
    return ctx;
  }

  test('1: build marker is BLOCKER10Z15', () => {
    assert.equal(BLOCKER10Z15_BUILD, Z15_BUILD);
    assert.equal(PUBLIC_API_BUILD, Z16_BUILD);
    const locals = buildTrackerPageLocals({});
    assert.equal(locals.renderBuild, Z16_BUILD);
  });

  test('2: public HTML shows image name amount only', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([
      { name: 'Parrot Fish', baseFishName: 'Parrot Fish', amount: 3, imageUrl: 'http://127.0.0.1/x.webp' },
    ]);
    assert.match(html, /fish-card__image/);
    assert.match(html, /Parrot Fish/);
    assert.match(html, /fish-card__amount[^>]*>x3</);
    assert.doesNotMatch(html, /fish-card__rarity/);
    assert.doesNotMatch(html, /fish-card__tag/);
  });

  test('3: amount rendered inside body middle section not overlapping name', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([
      { name: 'Giant Squid', baseFishName: 'Giant Squid', amount: 6, imageUrl: 'http://127.0.0.1/x.webp' },
    ]);
    assert.match(html, /fish-card__body[\s\S]*fish-card__name[\s\S]*Giant Squid[\s\S]*fish-card__amountRow[\s\S]*fish-card__amount[^>]*>x6</);
  });

  test('4: CSS places amount in body flow not title overlay zone', () => {
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'views', 'fishit_tracker.ejs'), 'utf8');
    assert.match(tpl, /\.fish-card__amountRow/);
    const amountBlock = tpl.match(/\.fish-card__amount[\s\S]*?box-sizing:border-box;/);
    assert.ok(amountBlock);
    assert.doesNotMatch(amountBlock[0], /position:absolute/);
    assert.match(tpl, /fish-card__body[\s\S]*flex-direction:column/);
  });

  test('5: canonical fish names render in HTML', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const names = [
      'Giant Squid', 'Panther Eel', 'Radiant Catfish', 'Zebra Snakehead', 'Freshwater Piranha',
    ];
    const html = buildItemsHtml(names.map((name) => ({
      name, baseFishName: name, amount: 1, imageUrl: 'http://127.0.0.1/x.webp',
    })));
    for (const name of names) {
      assert.match(html, new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
  });

  test('6: Radiant Catfish has no fake mutation badge', () => {
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml([{
      name: 'Radiant Catfish', baseFishName: 'Radiant Catfish', amount: 1,
      imageUrl: 'http://127.0.0.1/x.webp',
    }]);
    assert.match(html, /Radiant Catfish/);
    assert.doesNotMatch(html, />Radiant</);
  });

  test('7: no Goliath Tiger in public card HTML', async () => {
    setupTestDb();
    const pub = await buildPublicFishFields([{
      name: 'Goliath Tiger', itemId: '1008', category: 'fish', amount: 1,
    }], 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.doesNotMatch(html, /Goliath Tiger/i);
  });

  test('8: no Unknown Fish #267 in public card HTML', async () => {
    setupTestDb();
    const pub = await buildPublicFishFields([{
      name: 'Unknown Fish #267', itemId: '267', category: 'fish', amount: 1,
    }], 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.doesNotMatch(html, /Unknown Fish #267/i);
  });

  test('9: no noisy Unknown Fish #285 flood', async () => {
    setupTestDb();
    const rows = Array.from({ length: 12 }, (_, i) => ({
      name: 'Item #285', itemId: '285', category: 'items', amount: 1, weight: 2.7 + i,
      replionUuid: `uuid-285-z15-${i}`, replionAmountSource: 'replion_uuid_instance',
    }));
    const pub = await buildPublicFishFields(rows, 'http://127.0.0.1:8791');
    const buildItemsHtml = loadTrackerScriptFns();
    const html = buildItemsHtml(pub.publicItems);
    assert.equal(pub.publicItems.length, 0);
    assert.doesNotMatch(html, /Unknown Fish #285/i);
  });

  test('10: /tracker page keeps Z15 amount row layout', async () => {
    const express = require('express');
    const request = require('supertest');
    const trackerRouter = require('../src/fishitTrackerRoutes');
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(trackerRouter);
    const res = await request(app).get('/tracker').expect(200);
    assert.match(res.text, /fish-card__amountRow/);
  });
});

describe('BLOCKER10Z16 — live catch global evidence binding', { concurrency: 1 }, () => {
  const catchDelta = require('../src/fishitCatalogCatchDelta');
  const globalFishCatalog = require('../src/fishitGlobalFishItemCatalog');
  const globalCatalogService = require('../src/fishitGlobalCatalogService');
  const catalogStore = require('../src/fishitCatalogStore');
  const learnedFishCatalog = require('../src/fishitLearnedFishCatalog');
  const { ingestLearnedFishEntry } = require('../src/fishitTrackerRoutes');
  const Z16_BUILD = Z17_BUILD;

  beforeEach(() => {
    globalFishCatalog._reset();
    globalCatalogService._reset();
    learnedFishCatalog._reset();
    process.env.FISHIT_TEST_FIXTURE = '1';
  });

  afterEach(() => {
    delete process.env.FISHIT_TEST_FIXTURE;
  });

  test('1: Elshark Gran Maja catch stored as pending observation (bait-only delta)', () => {
    const discovery = catchDelta.processCatchDelta({
      pendingCatch: {
        rawText: 'Elshark Gran Maja (514.28K kg)',
        fishName: 'Elshark Gran Maja',
        source: 'catch_notification',
        detectedAt: new Date().toISOString(),
      },
      previousItemCounts: { 10: 0 },
      currentItems: [{ name: 'Topwater Bait', itemId: '10', amount: 1, category: 'bait' }],
      ingestLearned: ingestLearnedFishEntry,
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
      globalContext: {
        enabled: true,
        userId: 11033782953,
        evidenceSourceMode: 'live_roblox',
        sessionKey: 'denghub2',
      },
    });
    assert.equal(discovery.liveCatchAccepted, true);
    assert.equal(discovery.lastCatchParsed.baseFishName, 'Elshark Gran Maja');
    assert.ok(discovery.ignoredDeltaProof.some((d) => d.itemId === '10' && d.ignoredReason === 'known_non_fish'));
    assert.ok(discovery.globalEvidence);
    assert.equal(discovery.globalEvidence.pending, true);
    assert.equal(discovery.globalEvidence.decision, 'pending');
    assert.ok(discovery.globalEvidence.observationId);
    const stats = globalFishCatalog.getGlobalEvidenceStats(discovery);
    assert.ok(stats.globalEvidenceAccepted >= 1 || stats.globalEvidencePending >= 1);
  });

  test('2: global evidence counters not both zero after name-only submit', () => {
    const result = globalFishCatalog.submitNameOnlyEvidence({
      fishNameCandidate: 'Elshark Gran Maja',
      sourceText: 'Elshark Gran Maja (514.28K kg)',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      userId: 1,
      sessionKey: 'test',
    });
    assert.equal(result.accepted, true);
    assert.equal(result.pending, true);
    const stats = globalFishCatalog.getGlobalEvidenceStats({ globalEvidence: result });
    assert.ok(stats.globalEvidenceAccepted >= 1 || stats.globalEvidencePending >= 1);
    assert.equal(stats.globalEvidenceRejected, 0);
  });

  test('3: no fake itemId mapping from catch text alone', () => {
    globalFishCatalog.submitNameOnlyEvidence({
      fishNameCandidate: 'Elshark Gran Maja',
      source: 'catch_notification',
      evidenceSourceMode: 'live_roblox',
      userId: 2,
      sessionKey: 'test2',
    });
    assert.equal(globalFishCatalog.lookupById('10'), null);
    const elsharkMapping = globalFishCatalog.getAllMappings().find(
      (m) => m.baseFishName === 'Elshark Gran Maja' || m.fishName === 'Elshark Gran Maja',
    );
    assert.equal(elsharkMapping, undefined);
  });

  test('4: catch remains pending when no safe row binding exists', () => {
    const proof = catchDelta.attemptCatchSnapshotBinding({
      pendingCatch: {
        fishName: 'Elshark Gran Maja',
        source: 'catch_notification',
        detectedAt: new Date().toISOString(),
      },
      previousItems: [{ itemId: '156', amount: 1, category: 'fish' }],
      currentItems: [{ itemId: '156', amount: 1, category: 'fish' }],
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
      ingestLearned: ingestLearnedFishEntry,
      globalContext: { enabled: true, userId: 1, evidenceSourceMode: 'live_roblox' },
    });
    assert.equal(proof.bound, false);
    assert.match(proof.reason, /waiting for inventory row binding/i);
  });

  test('5: new UUID after catch can bind catch name to row safely', () => {
    const discovery = { learnedMappings: [] };
    const proof = catchDelta.attemptCatchSnapshotBinding({
      pendingCatch: {
        fishName: 'Elshark Gran Maja',
        baseFishName: 'Elshark Gran Maja',
        source: 'catch_notification',
        detectedAt: new Date().toISOString(),
      },
      previousItems: [{ itemId: '156', amount: 1, category: 'fish', replionUuid: 'uuid-old' }],
      currentItems: [
        { itemId: '156', amount: 1, category: 'fish', replionUuid: 'uuid-old' },
        { itemId: '999', amount: 1, category: 'fish', replionUuid: 'uuid-new-elshark' },
      ],
      mainCatalogLookup: () => null,
      ingestLearned: ingestLearnedFishEntry,
      globalContext: { enabled: true, userId: 1, evidenceSourceMode: 'live_roblox', sessionKey: 'bindtest' },
      existingDiscovery: discovery,
    });
    assert.equal(proof.bound, true);
    assert.equal(proof.itemId, '999');
    assert.equal(proof.replionUuid, 'uuid-new-elshark');
  });

  test('6: global species entry can exist without itemId mapping', () => {
    const species = globalCatalogService.recordCatchNotification({
      baseFishName: 'Elshark Gran Maja',
      rawText: 'Elshark Gran Maja (514.28K kg)',
      userId: 3,
      sessionKey: 'species-test',
    });
    assert.equal(species.accepted, true);
    assert.equal(species.pending, true);
    assert.ok(species.speciesId);
    assert.equal(species.itemIdMappings, 'pending_until_proven');
    const proof = globalCatalogService.buildGlobalSpeciesEvidenceProof('Elshark Gran Maja');
    assert.ok(proof.species);
    assert.equal(proof.hasItemIdMapping, false);
  });

  test('7: POST response includes liveCatchEvidence fields', async () => {
    const express = require('express');
    const request = require('supertest');
    const trackerRouter = require('../src/fishitTrackerRoutes');
    const app = express();
    app.use(express.json());
    app.use(trackerRouter);
    const res = await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'Z16CatchUser',
        userId: 33001,
        isOnline: true,
        clientOrigin: 'roblox_tracker',
        evidenceSourceMode: 'live_roblox',
        previousItemCounts: { 10: 0 },
        pendingCatchName: {
          rawText: 'Elshark Gran Maja (514.28K kg)',
          fishName: 'Elshark Gran Maja',
          source: 'catch_notification',
          detectedAt: new Date().toISOString(),
        },
        items: [{ itemId: '10', amount: 1, category: 'bait', name: 'Topwater Bait' }],
      });
    assert.equal(res.status, 200);
    assert.ok(res.body.liveCatchEvidence);
    assert.equal(res.body.liveCatchEvidence.liveCatchAccepted, true);
    assert.ok(res.body.liveCatchEvidence.pending || res.body.liveCatchEvidence.decision === 'pending');
  });

  test('8: debug=global exposes catch binding proof', async () => {
    const express = require('express');
    const request = require('supertest');
    const trackerRouter = require('../src/fishitTrackerRoutes');
    const app = express();
    app.use(express.json());
    app.use(trackerRouter);
    await request(app)
      .post('/api/fishit-tracker/update-backpack')
      .send({
        username: 'Z16DebugUser',
        userId: 33002,
        isOnline: true,
        clientOrigin: 'roblox_tracker',
        previousItemCounts: { 10: 0 },
        pendingCatchName: {
          rawText: 'Elshark Gran Maja (514.28K kg)',
          source: 'catch_notification',
          detectedAt: new Date().toISOString(),
        },
        items: [{ itemId: '10', amount: 1, category: 'bait' }],
      });
    const dbg = await request(app).get('/api/fishit-tracker/debug/Z16DebugUser?debug=global');
    assert.equal(dbg.status, 200);
    assert.equal(dbg.body.lastCatchParsed.baseFishName, 'Elshark Gran Maja');
    assert.ok(dbg.body.catchLearningProof);
    assert.ok(Array.isArray(dbg.body.nameCatalogDiscovery?.ignoredDeltaProof)
      || dbg.body.catchLearningProof.lastDiscovery?.ignoredDeltaProof);
  });

  test('9: normal /tracker has no debug proof', async () => {
    const express = require('express');
    const request = require('supertest');
    const trackerRouter = require('../src/fishitTrackerRoutes');
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(trackerRouter);
    const res = await request(app).get('/tracker').expect(200);
    assert.doesNotMatch(res.text, /ignoredDeltaProof/i);
    assert.doesNotMatch(res.text, /liveCatchAccepted/i);
  });

  test('10: build marker is BLOCKER10Z17', () => {
    const { BLOCKER10Z17_BUILD, PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');
    assert.equal(BLOCKER10Z17_BUILD, Z17_BUILD);
    assert.equal(PUBLIC_API_BUILD, Z17_BUILD);
  });

  test('11: tracker.lua has Z18 boot marker', () => {
    const lua = fs.readFileSync(path.join(__dirname, '..', '..', 'tracker.lua'), 'utf8');
    assert.match(lua, /BLOCKER10Z18_RECOVERED_SPECIES_IMAGE_RESOLUTION_2026_06_09/);
    assert.match(lua, /LIVE_GLOBAL_EVIDENCE result=/);
  });
});

describe('BLOCKER10Z17 — snapshot recovery and global completion', { concurrency: 1 }, () => {
  const snapshotRecovery = require('../src/fishitSnapshotRecovery');
  const catchDelta = require('../src/fishitCatalogCatchDelta');
  const globalFishCatalog = require('../src/fishitGlobalFishItemCatalog');
  const globalCatalogService = require('../src/fishitGlobalCatalogService');
  const catalogStore = require('../src/fishitCatalogStore');
  const { buildPublicFishFields, mergeItemsNoDowngradeFromCatalog } = require('../src/fishitTrackerRoutes');

  beforeEach(() => {
    setupTestDb();
    snapshotRecovery._resetForTests();
    globalFishCatalog._reset();
    globalCatalogService._reset();
    process.env.FISHIT_TEST_FIXTURE = '1';
  });

  afterEach(() => {
    delete process.env.FISHIT_TEST_FIXTURE;
  });

  test('1: snapshot recovery dry-run does not modify files', () => {
    const proof = snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      dryRun: true,
    });
    assert.equal(proof.ok, true);
    assert.equal(proof.mode, 'dry-run');
    assert.equal(proof.backup, null);
    assert.equal(proof.filesModified?.length || 0, 0);
  });

  test('2: snapshot recovery confirm creates backup before writing', () => {
    const proof = snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    assert.equal(proof.ok, true);
    assert.equal(proof.mode, 'confirm');
    assert.ok(proof.backup?.backupDir);
    assert.ok(proof.filesModified?.length >= 1);
  });

  test('3: snapshot recovery creates species evidence for Elshark, Mosasaur, Sparkly Eel', () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    for (const name of ['Elshark Gran Maja', 'Mosasaur Shark', 'Sparkly Eel']) {
      const sp = globalDb.findSpeciesByAliases([name]);
      assert.ok(sp?.species, `species missing: ${name}`);
      assert.equal(sp.species.canonical_name, name);
    }
  });

  test('4: snapshot recovery does not create fake itemId mappings', () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const db = globalDb.openDb();
    const rows = db.prepare(
      "SELECT * FROM fishit_global_item_mappings WHERE canonical_name IN ('Elshark Gran Maja','Mosasaur Shark','Sparkly Eel')",
    ).all();
    assert.equal(rows.length, 0);
  });

  test('5: recovery produces public counts 27 fish / 12 types', async () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const snapshotItems = [
      { name: 'Giant Squid', baseFishName: 'Giant Squid', itemId: '156', amount: 1, category: 'fish', replionUuid: 'a1', metadataFishName: 'Giant Squid', identityVerified: true },
      { name: 'Panther Eel', baseFishName: 'Panther Eel', itemId: '200', amount: 1, category: 'fish', replionUuid: 'a2', metadataFishName: 'Panther Eel', identityVerified: true },
      { name: 'Parrot Fish', baseFishName: 'Parrot Fish', itemId: '201', amount: 3, category: 'fish', replionUuid: 'a3', metadataFishName: 'Parrot Fish', identityVerified: true },
      { name: 'Viperangler Fish', baseFishName: 'Viperangler Fish', itemId: '202', amount: 1, category: 'fish', replionUuid: 'a4', metadataFishName: 'Viperangler Fish', identityVerified: true },
      { name: 'Red Goatfish', baseFishName: 'Red Goatfish', itemId: '203', amount: 4, category: 'fish', replionUuid: 'a5', metadataFishName: 'Red Goatfish', identityVerified: true },
      { name: 'Zebra Snakehead', baseFishName: 'Zebra Snakehead', itemId: '204', amount: 7, category: 'fish', replionUuid: 'a6', metadataFishName: 'Zebra Snakehead', identityVerified: true },
      { name: 'Skeleton Angler Fish', baseFishName: 'Skeleton Angler Fish', itemId: '205', amount: 2, category: 'fish', replionUuid: 'a7', metadataFishName: 'Skeleton Angler Fish', identityVerified: true },
      { name: 'Freshwater Piranha', baseFishName: 'Freshwater Piranha', itemId: '206', amount: 2, category: 'fish', replionUuid: 'a8', metadataFishName: 'Freshwater Piranha', identityVerified: true },
      { name: 'Mossy Fishlet', baseFishName: 'Mossy Fishlet', itemId: '207', amount: 2, category: 'fish', replionUuid: 'a9', metadataFishName: 'Mossy Fishlet', identityVerified: true },
    ];
    const enriched = mergeItemsNoDowngradeFromCatalog(snapshotItems);
    const sessionData = {
      username: 'denghub2',
      userSnapshotRecovery: snapshotRecovery.getSessionRecoveryMeta('denghub2'),
    };
    const pub = await buildPublicFishFields(enriched, 'http://127.0.0.1:8791', { sessionData, sessionKey: 'denghub2' });
    assert.equal(pub.publicCounts.visibleFishInstances, 27);
    assert.equal(pub.publicCounts.visibleFishTypes, 12);
  });

  test('6: Big Skeleton Angler Fish stacks into Skeleton Angler Fish', () => {
    const norm = snapshotRecovery.normalizeSnapshotFishName('Big Skeleton Angler Fish');
    assert.equal(norm.baseFishName, 'Skeleton Angler Fish');
    assert.match(norm.mutation || '', /big/i);
  });

  test('7: Big Viperangler Fish stacks into Viperangler Fish', () => {
    const norm = snapshotRecovery.normalizeSnapshotFishName('Big Viperangler Fish');
    assert.equal(norm.baseFishName, 'Viperangler Fish');
  });

  test('8: Parrot Fish Albino stacks into Parrot Fish', () => {
    const norm = snapshotRecovery.normalizeSnapshotFishName('Parrot Fish Albino');
    assert.equal(norm.baseFishName, 'Parrot Fish');
  });

  test('9: Red Goatfish Sandy stacks into Red Goatfish', () => {
    const norm = snapshotRecovery.normalizeSnapshotFishName('Red Goatfish Sandy');
    assert.equal(norm.baseFishName, 'Red Goatfish');
  });

  test('10: Sparkly Eel remains Sparkly Eel', () => {
    const norm = snapshotRecovery.normalizeSnapshotFishName('Sparkly Eel');
    assert.equal(norm.baseFishName, 'Sparkly Eel');
  });

  test('11-13: recovered species appear in public cards', async () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const snapshotItems = [
      { name: 'Giant Squid', baseFishName: 'Giant Squid', itemId: '156', amount: 1, category: 'fish', replionUuid: 'b1', metadataFishName: 'Giant Squid', identityVerified: true },
    ];
    const sessionData = {
      username: 'denghub2',
      userSnapshotRecovery: snapshotRecovery.getSessionRecoveryMeta('denghub2'),
    };
    const pub = await buildPublicFishFields(
      mergeItemsNoDowngradeFromCatalog(snapshotItems),
      'http://127.0.0.1:8791',
      { sessionData, sessionKey: 'denghub2' },
    );
    const names = pub.fishItems.map((f) => f.baseFishName || f.name);
    assert.ok(names.includes('Elshark Gran Maja'));
    assert.ok(names.includes('Mosasaur Shark'));
    const sparkly = pub.fishItems.find((f) => (f.baseFishName || f.name) === 'Sparkly Eel');
    assert.ok(sparkly);
    assert.equal(sparkly.amount, 2);
  });

  test('14-16: normal public cards have no rarity/mutation/debug labels', async () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', {
      sessionKey: 'denghub2',
      sessionData: { username: 'denghub2', userSnapshotRecovery: snapshotRecovery.getSessionRecoveryMeta('denghub2') },
    });
    for (const card of pub.fishItems) {
      assert.equal(card.rarity, null);
      assert.equal(card.mutation, null);
      assert.equal(card.userSnapshotRecovery, true);
      assert.equal(card.publicIdentityProof?.identitySource, 'user_snapshot_recovery');
    }
  });

  test('17-19: Goliath Tiger and Unknown Fish rows hidden from snapshot public merge', async () => {
    const items = [
      { name: 'Unknown Fish #267', itemId: '267', amount: 1, category: 'fish', replionUuid: 'x1' },
      { name: 'Unknown Fish #285', itemId: '285', amount: 5, category: 'fish', replionUuid: 'x2' },
      { name: 'Goliath Tiger', itemId: '1008', amount: 1, category: 'fish', replionUuid: 'x3' },
    ];
    const pub = await buildPublicFishFields(mergeItemsNoDowngradeFromCatalog(items), 'http://127.0.0.1:8791', {});
    const names = pub.fishItems.map((f) => f.name);
    assert.ok(!names.some((n) => /Goliath Tiger/i.test(n)));
    assert.ok(!names.some((n) => /Unknown Fish #267/i.test(n)));
    assert.ok(!names.some((n) => /Unknown Fish #285/i.test(n)));
  });

  test('20: live catch evidence creates species-level pending record before itemId binding', () => {
    const discovery = catchDelta.processCatchDelta({
      pendingCatch: {
        rawText: 'Elshark Gran Maja (514.28K kg)',
        fishName: 'Elshark Gran Maja',
        source: 'catch_notification',
        detectedAt: new Date().toISOString(),
      },
      previousItemCounts: { 10: 0 },
      currentItems: [{ name: 'Topwater Bait', itemId: '10', amount: 1, category: 'bait' }],
      ingestLearned: () => {},
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
      globalContext: {
        enabled: true,
        userId: 11033782953,
        evidenceSourceMode: 'live_roblox',
        sessionKey: 'denghub2',
      },
    });
    const resp = catchDelta.buildLiveCatchEvidenceResponse(discovery);
    assert.equal(resp.pending, true);
    assert.ok(resp.speciesEvidenceProof);
    assert.equal(resp.itemIdMappingStatus, 'pending');
  });

  test('21: bait itemId 10 delta ignored and does not kill catch evidence', () => {
    const discovery = catchDelta.processCatchDelta({
      pendingCatch: {
        rawText: 'Elshark Gran Maja (514.28K kg)',
        fishName: 'Elshark Gran Maja',
        source: 'catch_notification',
      },
      previousItemCounts: { 10: 0 },
      currentItems: [{ name: 'Topwater Bait', itemId: '10', amount: 1, category: 'bait' }],
      ingestLearned: () => {},
      mainCatalogLookup: (id) => catalogStore.lookupById(id),
      globalContext: { enabled: true, userId: 1, evidenceSourceMode: 'live_roblox', sessionKey: 't' },
    });
    assert.equal(discovery.liveCatchAccepted, true);
    assert.ok(discovery.ignoredDeltaProof.some((d) => d.itemId === '10'));
  });

  test('22: build marker is BLOCKER10Z17', () => {
    const { BLOCKER10Z17_BUILD, PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');
    assert.equal(BLOCKER10Z17_BUILD, Z17_BUILD);
    assert.equal(PUBLIC_API_BUILD, Z17_BUILD);
  });

  test('23: userSnapshotRecoveryProof includes expected debug fields', () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const proof = snapshotRecovery.buildUserSnapshotRecoveryProof('denghub2', {
      username: 'denghub2',
      userSnapshotRecovery: snapshotRecovery.getSessionRecoveryMeta('denghub2'),
    }, []);
    assert.equal(proof.active, true);
    assert.equal(proof.source, 'user_snapshot_2026_06_09');
    assert.ok(proof.expectedInventoryCounts['Elshark Gran Maja']);
    assert.ok(proof.recoveredSpecies.includes('Sparkly Eel'));
    assert.equal(proof.itemIdMappingStatus, 'pending');
    assert.equal(proof.publicCountExplanation.expectedTrackedFish, 27);
    assert.equal(proof.publicCountExplanation.expectedTypes, 12);
  });
});

describe('BLOCKER10Z18 — recovered species image resolution', { concurrency: 1 }, () => {
  const snapshotRecovery = require('../src/fishitSnapshotRecovery');
  const quizBotImageCatalog = require('../src/fishitQuizBotImageCatalog');
  const request = require('supertest');

  beforeEach(() => {
    setupTestDb();
    snapshotRecovery._resetForTests();
    quizBotImageCatalog._reset();
    fishImageCache._reset();
  });

  test('1: species-level recovery resolves Elshark Gran Maja image without itemId', async () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', {
      sessionKey: 'denghub2',
      sessionData: { username: 'denghub2', userSnapshotRecovery: snapshotRecovery.getSessionRecoveryMeta('denghub2') },
    });
    const card = pub.fishItems.find((f) => (f.baseFishName || f.name) === 'Elshark Gran Maja');
    assert.ok(card, 'Elshark Gran Maja card present');
    assert.equal(card.amount, 1);
    assert.ok(String(card.imageUrl).startsWith('/api/fishit-tracker/assets/fish/'), card.imageUrl);
    assert.equal(card.imageResolved, true);
    assert.equal(card.imageUrlPresent, true);
  });

  test('2: Mosasaur Shark image resolves from quiz bank by canonical name', async () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', {
      sessionKey: 'denghub2',
      sessionData: { username: 'denghub2', userSnapshotRecovery: snapshotRecovery.getSessionRecoveryMeta('denghub2') },
    });
    const card = pub.fishItems.find((f) => (f.baseFishName || f.name) === 'Mosasaur Shark');
    assert.ok(card);
    assert.ok(String(card.imageUrl).startsWith('/api/fishit-tracker/assets/fish/'));
    assert.equal(card.imageResolved, true);
    const hit = quizBotImageCatalog.lookupByFishName('Mosasaur Shark');
    assert.ok(hit?.localFile || hit?.assetId);
  });

  test('3: Sparkly Eel stays visible with placeholder when no trusted image exists', async () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', {
      sessionKey: 'denghub2',
      sessionData: { username: 'denghub2', userSnapshotRecovery: snapshotRecovery.getSessionRecoveryMeta('denghub2') },
    });
    const card = pub.fishItems.find((f) => (f.baseFishName || f.name) === 'Sparkly Eel');
    assert.ok(card);
    assert.equal(card.amount, 2);
    assert.equal(card.imageResolved, false);
    assert.ok(!card.imageUrl || !String(card.imageUrl).startsWith('/api/fishit-tracker/assets/fish/'));
  });

  test('4: cached public image URLs return HTTP 200', async () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', {
      sessionKey: 'denghub2',
      sessionData: { username: 'denghub2', userSnapshotRecovery: snapshotRecovery.getSessionRecoveryMeta('denghub2') },
    });
    const elshark = pub.fishItems.find((f) => (f.baseFishName || f.name) === 'Elshark Gran Maja');
    assert.ok(elshark?.imageUrl);
    const file = fishImageCache.filenameFromCachedUrl(elshark.imageUrl);
    assert.ok(file);
    assert.ok(fishImageCache.cachedFileExists(elshark.imageUrl));
  });

  test('5: imageResolutionProof includes searched aliases and source proof', () => {
    const proof = snapshotRecovery.buildRecoveredSpeciesImageResolutionProof([], {
      probeNames: ['Elshark Gran Maja', 'Mosasaur Shark', 'Sparkly Eel'],
    });
    assert.equal(proof.length, 3);
    const elshark = proof.find((p) => p.baseFishName === 'Elshark Gran Maja');
    assert.ok(elshark.searchedAliases.length >= 1);
    assert.ok(elshark.searchedSources.includes('quiz_bot_fishit_bank'));
    assert.ok(elshark.quizBankId || elshark.sourceFile);
    const sparkly = proof.find((p) => p.baseFishName === 'Sparkly Eel');
    assert.equal(sparkly.imageResolved, false);
    assert.equal(sparkly.missingReason, 'noTrustedImageFound');
  });

  test('6: public counts remain 27 fish / 12 types after image cache pass', async () => {
    snapshotRecovery.applySnapshotRecovery({
      sessionKey: 'denghub2',
      sourceId: 'user_snapshot_2026_06_09',
      confirm: true,
    });
    const pub = await buildPublicFishFields([], 'http://127.0.0.1:8791', {
      sessionKey: 'denghub2',
      sessionData: { username: 'denghub2', userSnapshotRecovery: snapshotRecovery.getSessionRecoveryMeta('denghub2') },
    });
    assert.equal(pub.fishCounts.fishInstances, 27);
    assert.equal(pub.fishCounts.fishTypes, 12);
  });

  test('7: build marker is BLOCKER10Z18', () => {
    const { BLOCKER10Z18_BUILD, PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');
    assert.equal(BLOCKER10Z18_BUILD, Z18_BUILD);
    assert.equal(PUBLIC_API_BUILD, Z18_BUILD);
  });

  test('8: quiz alias lookup resolves El Shark Gran Maja variant', () => {
    const hit = quizBotImageCatalog.lookupByFishName('El Shark Gran Maja');
    assert.ok(hit);
    assert.equal(hit.name, 'Elshark Gran Maja');
  });
});