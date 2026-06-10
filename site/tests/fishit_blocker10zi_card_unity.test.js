'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const { BLOCKER10ZI_BUILD } = require('../src/fishitTrackerBuild');
const { PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');

const FINAL_BUILD = 'BLOCKER10ZK_INVENTORY_MOBILE_BULK_APK_2026_06_09';
const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function loadTrackerCardFns() {
  const tpl = fs.readFileSync(TPL_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
  const helperNames = [
    'formatQuantity', 'formatAmountLabel', 'resolveItemAmount',
    'stoneDisplayName', 'formatWeightFromGrams', 'formatCardWeight', 'ownersChipHtml',
    'buildCardBadgesHtml', 'buildStoneStatsHtml', 'buildFishCardInnerHtml',
    'buildStoneCardInnerHtml', 'buildItemsHtml', 'cardKey', 'itemImageSrc', 'escHtml',
    'publicRarity', 'ftRarityClass', 'fishCardClassList',
  ];
  const helpers = helperNames.map((name) => script.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`)));
  for (let i = 0; i < helpers.length; i += 1) {
    assert.ok(helpers[i], `tracker template helper must exist: ${helperNames[i]}`);
  }
  return new Function(`
    const FT_RARITY_CLASS = { common:'ft-rarity-COMMON', uncommon:'ft-rarity-UNCOMMON', rare:'ft-rarity-RARE', epic:'ft-rarity-EPIC', legendary:'ft-rarity-LEGENDARY', legend:'ft-rarity-LEGENDARY', mythic:'ft-rarity-MYTHIC', secret:'ft-rarity-SECRET', forgotten:'ft-rarity-FORGOTTEN' };
    const STONE_DISPLAY_NAMES = { Double: 'Transcended Stone' };
    const PEOPLE_ICON_SVG = '<svg></svg>';
    const ITEM_IMAGES = { Default:'/assets/img/fishit/fallback-fish.svg' };
    function cardTitle(item){ return item.cardName||item.baseFishName||item.name||'Unknown'; }
    function publicRarity(item){ return item && item.rarity && item.rarity !== 'Unknown' ? item.rarity : null; }
    function ftRarityClass(r){ return r ? (FT_RARITY_CLASS[String(r).toLowerCase()] || 'ft-rarity-COMMON') : 'ft-rarity-COMMON'; }
    function fishCardClassList(item){
      const rarity = publicRarity(item);
      const rarityLow = rarity ? rarity.toLowerCase() : '';
      const cls = ['ft-card', 'ft-card--fish', ftRarityClass(rarity)];
      if (item.shiny === true && rarityLow !== 'secret') cls.push('shiny');
      return cls;
    }
    function isUsableImageUrl(url){
      if (!url || typeof url !== 'string') return false;
      const u = url.trim();
      return u.startsWith('http') || u.startsWith('/api/');
    }
    ${helpers.map((h) => h[0]).join('\n')}
    return { buildFishCardInnerHtml, buildStoneCardInnerHtml, buildItemsHtml, formatAmountLabel };
  `)();
}

describe('BLOCKER10ZI inventory card size unity', () => {
  test('build marker is BLOCKER10ZI', () => {
    assert.equal(BLOCKER10ZI_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
  });

  test('CSS uses ft-card fixed height grid and no legacy 138px tiles', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /grid-template-columns:repeat\(auto-fill,minmax\(230px,240px\)\)/);
    assert.match(tpl, /\.ft-card[\s\S]*height:84px/);
    assert.match(tpl, /\.ft-card--stone[\s\S]*height:72px/);
    assert.doesNotMatch(tpl, /height:138px/);
  });

  test('rendered fish cards use ft-card layout hooks', () => {
    const fns = loadTrackerCardFns();
    const html = fns.buildItemsHtml([
      { name: 'Skeleton Angler Fish', baseFishName: 'Skeleton Angler Fish', amount: 2, rarity: 'Epic', imageUrl: 'http://127.0.0.1/x.webp' },
      { name: 'Elshark Gran Maja', baseFishName: 'Elshark Gran Maja', amount: 1, rarity: 'Rare', imageUrl: 'http://127.0.0.1/y.webp' },
      { name: 'Zebra Snakehead', baseFishName: 'Zebra Snakehead', amount: 3, rarity: 'Uncommon', imageUrl: 'http://127.0.0.1/z.webp' },
    ]);
    assert.match(html, /ft-card ft-card--fish ft-rarity-EPIC/);
    assert.match(html, /ft-card-name[^>]*>Skeleton Angler Fish</);
    assert.match(html, /ft-card-stats/);
    assert.doesNotMatch(html, /inventory-card/);
  });
});
