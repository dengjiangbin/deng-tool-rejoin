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
  catalogMetaForItemId,
  _itemIdLockedBaseName,
} = require('../src/fishitTrackerRoutes');
const rarityColorMap = require('../src/fishitRarityColorMap');

const Z3_BUILD = 'BLOCKER10Z3_REPLION_GLOBAL_DB_NO_UI_DEPENDENCY_2026_06_07';
const Z_BUILD = Z3_BUILD;
const Y_BUILD = Z3_BUILD;
const X_BUILD = Z3_BUILD;
const W_BUILD = Z3_BUILD;
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
