'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const { formatQuantity, formatAmountLabel } = require('../src/fishitQuantityFormat');
const { BLOCKER10ZG_BUILD } = require('../src/fishitTrackerBuild');
const { PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');

const FINAL_BUILD = 'BLOCKER10ZK_INVENTORY_MOBILE_BULK_APK_2026_06_09';
const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function loadTrackerCardFns() {
  const tpl = fs.readFileSync(TPL_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
  const helperNames = [
    'formatQuantity', 'formatAmountLabel', 'resolveItemAmount', 'amountBadgeHtml',
    'stoneDisplayName', 'formatWeightFromGrams', 'formatCardWeight', 'cardChipHtml',
    'ownersChipHtml', 'buildCardBadgesHtml', 'buildStoneStatsHtml',
    'buildFishCardInnerHtml', 'buildStoneCardInnerHtml',
  ];
  const helpers = helperNames.map((name) => script.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`)));
  for (let i = 0; i < helpers.length; i += 1) {
    assert.ok(helpers[i], `tracker template helper must exist: ${helperNames[i]}`);
  }
  return new Function(`
    const CARD_RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', legend:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret', forgotten:'rarity-forgotten' };
    const STONE_DISPLAY_NAMES = { Double: 'Transcended Stone' };
    const PEOPLE_ICON_SVG = '<svg></svg>';
    const ITEM_IMAGES = { Default:'/assets/img/fishit/fallback-fish.svg' };
    function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
    function cardTitle(item){ return item.cardName||item.baseFishName||item.name||'Unknown'; }
    function publicRarity(item){ return item && item.rarity && item.rarity !== 'Unknown' ? item.rarity : 'Unknown'; }
    function rarityNameStyle(){ return ''; }
    function isUsableImageUrl(url){ return typeof url==='string' && (url.startsWith('http') || url.startsWith('/api/')); }
    function itemImageSrc(item){ return isUsableImageUrl(item.imageUrl)?item.imageUrl:null; }
    ${helpers.map((h) => h[0]).join('\n')}
    return { formatQuantity, formatAmountLabel, buildFishCardInnerHtml, buildStoneCardInnerHtml, amountBadgeHtml };
  `)();
}

describe('BLOCKER10ZE quantity format + card chip badges', () => {
  test('build marker is BLOCKER10ZG', () => {
    assert.equal(BLOCKER10ZG_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
  });

  test('formatAmountLabel uses detailed en-US separators', () => {
    assert.equal(formatAmountLabel(1), 'x1');
    assert.equal(formatAmountLabel(135), 'x135');
    assert.equal(formatAmountLabel(1000), 'x1,000');
    assert.equal(formatAmountLabel(12345), 'x12,345');
    assert.equal(formatAmountLabel(1234567), 'x1,234,567');
    assert.equal(formatQuantity(1000), '1,000');
  });

  test('fish card amount uses formatter in template render', () => {
    const fns = loadTrackerCardFns();
    const html = fns.buildFishCardInnerHtml({
      name: 'Giant Squid',
      baseFishName: 'Giant Squid',
      amount: 1000,
      imageUrl: 'http://127.0.0.1/x.webp',
    });
    assert.match(html, /ft-chip-qty[^>]*>x1,000</);
    assert.match(html, /ft-card-stats/);
    assert.doesNotMatch(html, /fish-card__amount/);
  });

  test('stone card amount uses formatter in template render', () => {
    const fns = loadTrackerCardFns();
    const html = fns.buildStoneCardInnerHtml({
      name: 'Normal Enchant Stone',
      displayName: 'Normal Enchant Stone',
      quantity: 12345,
      imageUrl: '/api/fishit-tracker/assets/stones/stone_10_normal.png',
    });
    assert.match(html, /ft-chip-qty[^>]*>x12,345</);
    assert.match(html, /ft-card-name/);
  });

  test('ft-chip CSS uses inline badge row inside card content', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /\.ft-card-stats[\s\S]*display:flex/);
    assert.match(tpl, /\.ft-chip[\s\S]*border-radius:6px/);
    assert.match(tpl, /function formatAmountLabel/);
    assert.match(tpl, /function buildCardBadgesHtml/);
  });

  test('fish and stone cards use fixed height contract', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /\.ft-card[\s\S]*height:84px/);
    assert.match(tpl, /\.ft-card--stone[\s\S]*height:72px/);
    assert.match(tpl, /\.ft-card-main[\s\S]*display:flex/);
    assert.doesNotMatch(tpl, /height:138px/);
  });
});
