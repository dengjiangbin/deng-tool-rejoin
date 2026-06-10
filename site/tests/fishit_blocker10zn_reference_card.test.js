'use strict';

const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const express = require('express');
const request = require('supertest');

const { BLOCKER10ZN_REFERENCE_CARD_UI_MATCH_BUILD } = require('../src/fishitTrackerBuild');
const { CLEAN_TRACKER_LOADSTRING } = require('../src/fishitTrackerLoadstring');
const trackerRouter = require('../src/fishitTrackerRoutes');

const TPL_PATH = path.join(__dirname, '..', 'views', 'fishit_tracker.ejs');
const MARKER = 'BLOCKER10ZN_REFERENCE_CARD_UI_MATCH_2026_06_10';

function loadFishCardFns() {
  const tpl = fs.readFileSync(TPL_PATH, 'utf8');
  const script = tpl.slice(tpl.indexOf('<script>'), tpl.indexOf('</script>') + 9);
  const names = [
    'formatQuantity', 'formatAmountLabel', 'resolveItemAmount', 'stoneDisplayName',
    'formatWeightFromGrams', 'formatCardWeight', 'ownersChipHtml', 'buildCardBadgesHtml', 'buildFishCardInnerHtml',
    'buildItemsHtml', 'cardKey', 'itemImageSrc', 'escHtml', 'publicRarity', 'ftRarityClass', 'fishCardClassList',
  ];
  const blocks = names.map((name) => script.match(new RegExp(`function ${name}\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}`)));
  blocks.forEach((block, i) => assert.ok(block, `missing helper ${names[i]}`));
  return new Function(`
    const FT_RARITY_CLASS = { common:'ft-rarity-COMMON', uncommon:'ft-rarity-UNCOMMON', rare:'ft-rarity-RARE', epic:'ft-rarity-EPIC', legendary:'ft-rarity-LEGENDARY', legend:'ft-rarity-LEGENDARY', mythic:'ft-rarity-MYTHIC', secret:'ft-rarity-SECRET', forgotten:'ft-rarity-FORGOTTEN' };
    const STONE_DISPLAY_NAMES = { Double: 'Transcended Stone' };
    const PEOPLE_ICON_SVG = '<svg class="ft-chip-icon"></svg>';
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
    function isUsableImageUrl(url){ return typeof url === 'string' && (url.startsWith('http') || url.startsWith('/')); }
    ${blocks.map((b) => b[0]).join('\n')}
    return { buildFishCardInnerHtml, buildItemsHtml, buildCardBadgesHtml };
  `)();
}

describe('BLOCKER10ZN reference card UI', () => {
  test('build marker is BLOCKER10ZN', () => {
    assert.equal(BLOCKER10ZN_REFERENCE_CARD_UI_MATCH_BUILD, MARKER);
  });

  test('template uses ft-card flex layout and removes old fish-card tile CSS', () => {
    const tpl = fs.readFileSync(TPL_PATH, 'utf8');
    assert.match(tpl, /BLOCKER10ZN_REFERENCE_CARD_UI_MATCH_2026_06_10/);
    assert.match(tpl, /\.ft-card[\s\S]*height:84px[\s\S]*display:flex/);
    assert.match(tpl, /\.ft-card-icon[\s\S]*54px[\s\S]*object-fit:contain/);
    assert.match(tpl, /\.ft-card-stats[\s\S]*display:flex/);
    assert.match(tpl, /grid-template-columns:repeat\(auto-fill,minmax\(230px,240px\)\)/);
    assert.match(tpl, /\.ft-rarity-SECRET[\s\S]*#16d487/);
    assert.doesNotMatch(tpl, /\.fish-card__imageWrap/);
    assert.doesNotMatch(tpl, /height:138px/);
    assert.doesNotMatch(tpl, /position:\s*absolute[\s\S]*qty/);
  });

  test('fish card markup matches reference structure', () => {
    const fns = loadFishCardFns();
    const html = fns.buildFishCardInnerHtml({
      name: 'Skeleton Narwhal',
      amount: 13,
      rarity: 'Secret',
      debugWeight: { display: '4.4M' },
      imageUrl: 'http://127.0.0.1/fish.webp',
    });
    assert.match(html, /class="ft-card-icon"/);
    assert.match(html, /class="ft-card-main"/);
    assert.match(html, /class="ft-card-name"[^>]*>Skeleton Narwhal</);
    assert.match(html, /class="ft-card-stats"/);
    assert.match(html, /class="ft-chip ft-chip-qty">x13</);
    assert.match(html, /class="ft-chip ft-chip-rarity">Secret</);
    assert.match(html, /class="ft-card-weight">4\.4M</);
  });

  test('live fish examples render ft-card without old inventory-card classes', () => {
    const fns = loadFishCardFns();
    const samples = [
      { name: 'Elshark Gran Maja', amount: 2, rarity: 'Legendary', imageUrl: 'http://127.0.0.1/a.webp' },
      { name: 'Skeleton Narwhal', amount: 13, rarity: 'Secret', debugWeight: { display: '4.4M' }, imageUrl: 'http://127.0.0.1/b.webp' },
      { name: 'Maze Angelfish', amount: 1, rarity: 'Epic', imageUrl: 'http://127.0.0.1/c.webp' },
      { name: 'Starfish', amount: 5, rarity: 'Common', imageUrl: 'http://127.0.0.1/d.webp' },
      { name: 'White Clownfish', amount: 3, rarity: 'Rare', imageUrl: 'http://127.0.0.1/e.webp' },
      { name: 'Lion Fish', amount: 1, rarity: 'Uncommon', imageUrl: 'http://127.0.0.1/f.webp' },
    ];
    const html = fns.buildItemsHtml(samples);
    assert.match(html, /ft-card ft-card--fish ft-rarity-SECRET/);
    assert.match(html, /Skeleton Narwhal/);
    assert.doesNotMatch(html, /fish-card/);
    assert.doesNotMatch(html, /inventory-card/);
    assert.doesNotMatch(html, /amount-badge/);
  });

  test('/tracker exposes BLOCKER10ZN marker and protected loader unchanged', async () => {
    const app = express();
    app.set('view engine', 'ejs');
    app.set('views', path.join(__dirname, '..', 'views'));
    app.use(trackerRouter);
    const res = await request(app).get('/tracker').expect(200);
    assert.match(res.text, new RegExp(MARKER));
    assert.match(res.text, /ft-card--fish/);
    assert.match(res.text, /dist\/tracker\.lua/);
    assert.match(res.text, new RegExp(CLEAN_TRACKER_LOADSTRING.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.doesNotMatch(res.text, /tracker\.luraph\.lua/);
  });
});
