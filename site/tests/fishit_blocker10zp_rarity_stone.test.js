'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const trackerRarityStyle = require('../src/fishitTrackerRarityStyle');
const stoneDisplayMap = require('../src/fishitStoneDisplayMap');
const stoneImageAssets = require('../src/fishitStoneImageAssets');
const { buildPublicFishFields } = require('../src/fishitTrackerRoutes');
const gameItemDbPublic = require('../src/fishitGameItemDbPublic');
const {
  BLOCKER10ZP_RARITY_MAPPING_AND_TRANSCENDED_STONE_IMAGE_FIX_MARKER,
  BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER,
} = require('../src/fishitTrackerBuild');

const TRACKER_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const BASE_URL = 'http://127.0.0.1:8791';

function loadTrackerCardFns() {
  const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
  const helperNames = [
    'formatQuantity', 'formatAmountLabel', 'resolveItemAmount',
    'stoneDisplayName', 'buildCardBadgesHtml', 'buildFishCardInnerHtml', 'formatCardWeight',
    'fishCardClassList', 'cardTitle', 'publicRarity', 'itemImageSrc', 'escHtml',
  ];
  const helpers = helperNames.map((name) => script.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`)));
  for (let i = 0; i < helpers.length; i += 1) {
    assert.ok(helpers[i], `tracker template helper must exist: ${helperNames[i]}`);
  }
  return new Function(`
    const ITEM_IMAGES = { Default:'/assets/img/fishit/fallback-fish.svg' };
    function escHtml(s){ return String(s||''); }
    function publicRarity(item){ return item && item.rarity && item.rarity !== 'Unknown' ? item.rarity : 'Unknown'; }
    function isUsableImageUrl(url){ return typeof url==='string' && (url.startsWith('http') || url.startsWith('/api/')); }
    ${trackerRarityStyle.buildTrackerRarityJsBootstrap()}
    ${helpers.map((h) => h[0]).join('\n')}
    return { ftRarityClass, fishCardClassList, buildFishCardInnerHtml, stoneDisplayName };
  `)();
}

function stoneRow(type, itemId, qty = 1, overrides = {}) {
  return {
    kind: 'stone',
    itemId: String(itemId),
    name: overrides.name || `${type} Enchant Stone`,
    stoneType: type,
    quantity: qty,
    icon: overrides.icon || 'rbxassetid://73883190545629',
    source: 'playerdata_gameitemdb',
    identityVerified: true,
    ...overrides,
  };
}

describe('BLOCKER10ZP rarity mapping + Transcended Stone image', () => {
  test('build marker is BLOCKER10ZP and exposed on tracker deploy marker', () => {
    assert.equal(
      BLOCKER10ZP_RARITY_MAPPING_AND_TRANSCENDED_STONE_IMAGE_FIX_MARKER,
      'BLOCKER10ZP_RARITY_MAPPING_AND_TRANSCENDED_STONE_IMAGE_FIX_2026_06_10',
    );
    assert.equal(BLOCKER10ZB_LIVE_TRACKER_UI_DEPLOY_MARKER, BLOCKER10ZP_RARITY_MAPPING_AND_TRANSCENDED_STONE_IMAGE_FIX_MARKER);
    const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
    assert.match(tpl, /BLOCKER10ZP_RARITY_MAPPING_AND_TRANSCENDED_STONE_IMAGE_FIX_2026_06_10/);
  });

  test('Epic resolves to purple ft-card class and background', () => {
    assert.equal(trackerRarityStyle.ftRarityClass('Epic'), 'ft-rarity-EPIC');
    assert.equal(trackerRarityStyle.ftRarityBackground('Epic'), '#9333ea');
    const css = trackerRarityStyle.buildFtCardRarityCss();
    const epicLine = css.split('\n').find((line) => line.includes('.ft-rarity-EPIC'));
    assert.ok(epicLine);
    assert.match(epicLine, /background:#9333ea/);
    assert.doesNotMatch(epicLine, /#dc2626/);
  });

  test('Mythic resolves to red ft-card class and background', () => {
    assert.equal(trackerRarityStyle.ftRarityClass('Mythic'), 'ft-rarity-MYTHIC');
    assert.equal(trackerRarityStyle.ftRarityBackground('Mythic'), '#dc2626');
    const css = trackerRarityStyle.buildFtCardRarityCss();
    assert.match(css, /\.ft-rarity-MYTHIC[\s\S]*background:#dc2626/);
    assert.doesNotMatch(css, /\.ft-rarity-MYTHIC[\s\S]*background:#9333ea/);
  });

  test('Epic card render proof does not use red class; Mythic does not use purple', () => {
    const fns = loadTrackerCardFns();
    const epicClasses = fns.fishCardClassList({ rarity: 'Epic', name: 'Panther Grouper' });
    const mythicClasses = fns.fishCardClassList({ rarity: 'Mythic', name: 'Mystic Squid' });
    assert.ok(epicClasses.includes('ft-rarity-EPIC'));
    assert.ok(!epicClasses.includes('ft-rarity-MYTHIC'));
    assert.ok(mythicClasses.includes('ft-rarity-MYTHIC'));
    assert.ok(!mythicClasses.includes('ft-rarity-EPIC'));
    const epicHtml = fns.buildFishCardInnerHtml({
      name: 'Panther Grouper',
      baseFishName: 'Panther Grouper',
      rarity: 'Epic',
      amount: 1,
      imageUrl: 'http://127.0.0.1/x.webp',
    });
    assert.match(epicHtml, /ft-chip-rarity[^>]*>Epic</);
    assert.doesNotMatch(epicHtml, /ft-rarity-MYTHIC/);
  });

  test('Double internal stone type displays Transcended Stone and transcended image asset', async () => {
    assert.equal(stoneDisplayMap.publicStoneDisplayName({ stoneType: 'Double' }), 'Transcended Stone');
    assert.equal(
      stoneDisplayMap.publicStoneImageFilename({ itemId: '246', stoneType: 'Double' }),
      'stone_246_transcended.png',
    );
    assert.ok(stoneImageAssets.stoneAssetFileExists('stone_246_transcended.png'));

    const pub = await buildPublicFishFields([], BASE_URL, {
      sessionData: {
        inventorySource: 'playerdata_gameitemdb',
        playerDataFishItems: [],
        playerDataStoneItems: [stoneRow('Double', 246, 2)],
        sourceTruth: gameItemDbPublic.defaultSourceTruth(),
      },
    });
    const stone = pub.stoneItems.find((s) => s.itemId === '246');
    assert.ok(stone);
    assert.equal(stone.displayName, 'Transcended Stone');
    assert.equal(stone.name, 'Transcended Stone');
    assert.match(stone.imageUrl, /\/api\/fishit-tracker\/assets\/stones\/stone_246_transcended\.png$/);
    assert.doesNotMatch(stone.imageUrl, /stone_246_double\.png/);
  });

  test('tracker template does not expose old Double Enchant Stone label publicly', () => {
    const tpl = fs.readFileSync(TRACKER_PATH, 'utf8');
    assert.doesNotMatch(tpl, /Double Enchant Stone/);
    assert.equal(stoneDisplayMap.publicStoneDisplayName({ stoneType: 'Double', name: 'Double Enchant Stone' }), 'Transcended Stone');
  });

  test('legacy double stone filename is not used by lookupStoneAsset', () => {
    const asset = stoneImageAssets.lookupStoneAsset('246', 'Double');
    assert.equal(asset.filename, 'stone_246_transcended.png');
    assert.notEqual(asset.filename, 'stone_246_double.png');
  });
});
