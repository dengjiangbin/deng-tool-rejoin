'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const { BLOCKER10ZI_BUILD } = require('../src/fishitTrackerBuild');
const { PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');

const FINAL_BUILD = 'BLOCKER10ZI_INVENTORY_CARD_UNITY_2026_06_09';
const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');

function loadTrackerCardFns() {
  const tpl = fs.readFileSync(TPL_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
  const helpers = [
    script.match(/function formatQuantity\(value\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function formatAmountLabel\(value\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function resolveItemAmount\(item\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function amountBadgeHtml\(item\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function buildFishCardInnerHtml\(item\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function buildStoneCardInnerHtml\(item\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function buildItemsHtml\(items\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function cardKey\(item\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function itemImageSrc\(item\)\s*\{[\s\S]*?\n  \}/),
    script.match(/function escHtml\(s\)\s*\{[\s\S]*?\n  \}/),
  ];
  for (const block of helpers) {
    assert.ok(block, 'tracker template helper must exist');
  }
  return new Function(`
    const CARD_RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', legend:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret', forgotten:'rarity-forgotten' };
    const ITEM_IMAGES = { Default:'/assets/img/fishit/fallback-fish.svg' };
    function cardTitle(item){ return item.cardName||item.baseFishName||item.name||'Unknown'; }
    function cardRarityClass(r){ return r ? (CARD_RARITY_MAP[String(r).toLowerCase()] || 'rarity-common') : 'rarity-common'; }
    function rarityNameStyle(){ return ''; }
    function isUsableImageUrl(url){
      if (!url || typeof url !== 'string') return false;
      const u = url.trim();
      return u.startsWith('http') || u.startsWith('/api/');
    }
    ${helpers.map((h) => h[0]).join('\n')}
    return { buildFishCardInnerHtml, buildStoneCardInnerHtml, buildItemsHtml, amountBadgeHtml, formatAmountLabel };
  `)();
}

describe('BLOCKER10ZI inventory card size unity', () => {
  test('build marker is BLOCKER10ZI', () => {
    assert.equal(BLOCKER10ZI_BUILD, FINAL_BUILD);
    assert.equal(PUBLIC_API_BUILD, FINAL_BUILD);
  });

  test('CSS uses fixed equal grid columns and card dimensions', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /\.inventory-grid[\s\S]*grid-template-columns:repeat\(auto-fill,minmax\(220px,220px\)\)/);
    assert.match(tpl, /\.fish-card[\s\S]*width:220px[\s\S]*height:138px[\s\S]*max-height:138px/);
    assert.match(tpl, /\.stone-card[\s\S]*width:220px/);
    assert.match(tpl, /\.inventory-card[\s\S]*max-width:220px/);
    assert.doesNotMatch(tpl, /\.fish-card[\s\S]*height:auto/);
    assert.doesNotMatch(tpl, /\.fish-card[\s\S]*min-height:108px/);
  });

  test('CSS fixes image box and name clamp without content-driven card height', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /\.fish-card__image[\s\S]*width:78px[\s\S]*height:78px[\s\S]*object-fit:contain/);
    assert.match(tpl, /\.inventory-card-name/);
    assert.match(tpl, /\.fish-card__name[\s\S]*max-height:42px[\s\S]*-webkit-line-clamp:2/);
    assert.match(tpl, /\.fish-card \.amount-badge[\s\S]*position:absolute[\s\S]*left:14px[\s\S]*bottom:14px/);
    assert.match(tpl, /@media \(max-width:640px\)[\s\S]*minmax\(160px,1fr\)/);
  });

  test('rendered fish cards share inventory-card class and fixed layout hooks', () => {
    const fns = loadTrackerCardFns();
    const html = fns.buildItemsHtml([
      { name: 'Skeleton Angler Fish', baseFishName: 'Skeleton Angler Fish', amount: 2, rarity: 'Epic', imageUrl: 'http://127.0.0.1/x.webp' },
      { name: 'Elshark Gran Maja', baseFishName: 'Elshark Gran Maja', amount: 1, rarity: 'Rare', imageUrl: 'http://127.0.0.1/y.webp' },
      { name: 'Zebra Snakehead', baseFishName: 'Zebra Snakehead', amount: 3, rarity: 'Uncommon', imageUrl: 'http://127.0.0.1/z.webp' },
    ]);
    assert.match(html, /items-grid inventory-grid fish-grid/);
    assert.match(html, /fish-card inventory-card rarity-epic/);
    assert.match(html, /inventory-card-name[^>]*>Skeleton Angler Fish</);
    assert.match(html, /inventory-card-name[^>]*>Elshark Gran Maja</);
    assert.match(html, /inventory-card-name[^>]*>Zebra Snakehead</);
    assert.match(html, /inventory-card-image/);
    const cardCount = (html.match(/class="item-card fish-card inventory-card/g) || []).length;
    assert.equal(cardCount, 3);
  });

  test('stone cards use the same inventory-card sizing contract as fish cards', () => {
    const fns = loadTrackerCardFns();
    const html = fns.buildStoneCardInnerHtml({
      name: 'Normal Enchant Stone',
      displayName: 'Normal Enchant Stone',
      quantity: 1000,
      imageUrl: '/api/fishit-tracker/assets/stones/stone_10_normal.png',
    });
    assert.match(html, /inventory-card-image-wrap/);
    assert.match(html, /inventory-card-image stone-card__image/);
    assert.match(html, /inventory-card-name stone-card__name|stone-card__name inventory-card-name/);
    assert.match(html, /amount-badge[^>]*>x1,000</);
  });

  test('long names and x1,000 amount do not add layout growth hooks', () => {
    const fns = loadTrackerCardFns();
    const longNameHtml = fns.buildFishCardInnerHtml({
      name: 'Skeleton Angler Fish',
      baseFishName: 'Skeleton Angler Fish',
      amount: 1000,
      imageUrl: 'http://127.0.0.1/x.webp',
    });
    assert.match(longNameHtml, /inventory-card-name/);
    assert.match(longNameHtml, /amount-badge[^>]*>x1,000</);
    assert.doesNotMatch(longNameHtml, /fish-card__amountRow/);
    assert.doesNotMatch(longNameHtml, /height:auto/);
  });
});
