'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const { formatQuantity, formatAmountLabel } = require('../src/fishitQuantityFormat');
const { BLOCKER10ZG_BUILD } = require('../src/fishitTrackerBuild');
const { PUBLIC_API_BUILD } = require('../src/fishitTrackerRoutes');

const FINAL_BUILD = 'BLOCKER10ZJ_INVENTORY_SEARCH_MENU_STATS_APK_2026_06_09';
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
  ];
  for (const block of helpers) {
    assert.ok(block, 'tracker template helper must exist');
  }
  return new Function(`
    const CARD_RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'rarity-secret', forgotten:'rarity-forgotten' };
    const RARITY_MAP = { common:'rarity-common', uncommon:'rarity-uncommon', rare:'rarity-rare', epic:'rarity-epic', legendary:'rarity-legendary', mythic:'rarity-mythic', secret:'badge-rarity-secret', forgotten:'rarity-forgotten' };
    const RARITY_NAME_COLORS = {};
    const ITEM_IMAGES = { Default:'/assets/img/fishit/fallback-fish.svg' };
    function escHtml(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
    function cardTitle(item){ return item.cardName||item.baseFishName||item.name||'Unknown'; }
    function rarityNameStyle(){ return ''; }
    function isUsableImageUrl(url){ return typeof url==='string' && (url.startsWith('http') || url.startsWith('/api/')); }
    function itemImageSrc(item){ return isUsableImageUrl(item.imageUrl)?item.imageUrl:null; }
    ${helpers.map((h) => h[0]).join('\n')}
    return { formatQuantity, formatAmountLabel, buildFishCardInnerHtml, buildStoneCardInnerHtml, amountBadgeHtml };
  `)();
}

describe('BLOCKER10ZE quantity format + bottom amount badges', () => {
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
    assert.match(html, /amount-badge[^>]*>x1,000</);
    assert.doesNotMatch(html, /fish-card__amountRow/);
  });

  test('stone card amount uses formatter in template render', () => {
    const fns = loadTrackerCardFns();
    const html = fns.buildStoneCardInnerHtml({
      name: 'Normal Enchant Stone',
      displayName: 'Normal Enchant Stone',
      quantity: 12345,
      imageUrl: '/api/fishit-tracker/assets/stones/stone_10_normal.png',
    });
    assert.match(html, /amount-badge[^>]*>x12,345</);
    assert.match(html, /stone-card__name/);
  });

  test('amount-badge CSS is bottom-left for fish and stone cards', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    const badgeBlock = tpl.match(/\.fish-card \.amount-badge[\s\S]*?box-sizing:border-box;/);
    assert.ok(badgeBlock, 'amount-badge CSS block must exist');
    assert.match(badgeBlock[0], /position:absolute/);
    assert.match(badgeBlock[0], /bottom:14px/);
    assert.match(badgeBlock[0], /left:14px/);
    assert.match(badgeBlock[0], /top:auto/);
    assert.match(badgeBlock[0], /transform:none/);
    assert.match(badgeBlock[0], /white-space:nowrap/);
    assert.match(badgeBlock[0], /text-overflow:ellipsis/);
    assert.doesNotMatch(badgeBlock[0], /top:\s*50%/);
    assert.doesNotMatch(badgeBlock[0], /translateY\(/);
    assert.match(tpl, /\.stone-card \.amount-badge/);
    assert.match(tpl, /function formatAmountLabel/);
    assert.match(tpl, /function amountBadgeHtml/);
  });

  test('fish and stone cards use fixed height contract for badge placement', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /\.fish-card[\s\S]*height:138px[\s\S]*max-height:138px/);
    assert.match(tpl, /\.stone-card[\s\S]*height:138px/);
    assert.match(tpl, /\.fish-card__body[\s\S]*display:contents/);
  });
});
