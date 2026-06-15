'use strict';

// Renders the shipped tracker UI states using the REAL compiled inventory CSS
// and the REAL Runic Stone override image, then captures screenshots with
// Playwright so the STRICT TRACKER FIX has visual proof.
//   node scripts/gen_tracker_fix_proof.js

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.join(__dirname, '..');
const manifest = require('../src/inventoryAssetManifest.json');
const CSS_PATH = path.join(ROOT, 'public', 'assets', manifest.css);
const RUNIC_IMG = path.join(ROOT, 'data', 'manual_image_seed', 'runic_stone_2026_06_15_v2.png');
const OUT_DIR = path.join(ROOT, 'public', 'proof');

const css = fs.readFileSync(CSS_PATH, 'utf8');
const runicUrl = 'data:image/png;base64,' + fs.readFileSync(RUNIC_IMG).toString('base64');

function fishCard(mut, color, name, owner, weight) {
  const mutLine = mut
    ? `<div class="ft-inst-card__mut" style="color:${color}">${mut}</div>`
    : '<div class="ft-inst-card__mut" aria-hidden="true"></div>';
  return `<div class="ft-inst-card">${mutLine}<div class="ft-inst-card__name">${name}</div>`
    + `<div class="ft-inst-card__owner">${owner}</div>`
    + `<div class="ft-inst-card__weight">Weight: ${weight} kg</div></div>`;
}

function breakdownRow(mut, color, owner, qty) {
  const mutLine = mut ? `<span class="ft-detail-owner__mut" style="color:${color}">${mut}</span>` : '';
  return `<div class="ft-detail-owner"><span class="ft-detail-owner__main">${mutLine}`
    + `<span class="ft-detail-owner__name">${owner}</span></span>`
    + `<span class="ft-detail-owner__qty">x${qty}</span></div>`;
}

// Sorted by owner asc, then mutation, then weight desc — same as ftSortFishCards.
const fishCards = [
  fishCard('Gold', '#fbbf24', 'Whale Shark', 'alice', '2210.4'),
  fishCard('Frozen', '#67e8f9', 'Whale Shark', 'alice', '1980.1'),
  fishCard('', '', 'Whale Shark', 'alice', '1502.7'),
  fishCard('Ruby', '#f87171', 'Whale Shark', 'bob', '2105.9'),
  fishCard('Shiny', '#fde68a', 'Whale Shark', 'bob', '1740.3'),
  fishCard('', '', 'Whale Shark', 'bob', '1330.0'),
  fishCard('Corrupt', '#c084fc', 'Whale Shark', 'carol', '1899.2'),
  fishCard('Gold', '#fbbf24', 'Whale Shark', 'carol', '1610.8'),
].join('');

const fishDetailHtml = `
<section class="ft-detail-panel">
  <div class="ft-detail-panel__head">
    <button type="button" class="ft-detail-back"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="15 18 9 12 15 6"></polyline></svg>Back</button>
    <div class="ft-detail-icon"><span class="ft-detail-icon__fallback">&#x1F420;</span></div>
    <div class="ft-detail-headtext">
      <div class="ft-detail-tag" style="color:#93c5fd">Fish</div>
      <div class="ft-detail-name">Whale Shark</div>
      <div class="ft-detail-count">8 fish</div>
    </div>
  </div>
  <div class="ft-detail-instances">${fishCards}</div>
</section>`;

const totemDetailHtml = `
<section class="ft-detail-panel">
  <div class="ft-detail-panel__head">
    <button type="button" class="ft-detail-back"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="15 18 9 12 15 6"></polyline></svg>Back</button>
    <div class="ft-detail-icon"><img src="${runicUrl}" alt="Runic Stone"></div>
    <div class="ft-detail-headtext">
      <div class="ft-detail-tag" style="color:#fde68a">Shiny</div>
      <div class="ft-detail-name">Totem</div>
      <div class="ft-detail-count">Total x44</div>
    </div>
  </div>
  <div class="ft-detail-owners">
    ${breakdownRow('Shiny', '#fde68a', 'alice', 12)}
    ${breakdownRow('Shiny', '#fde68a', 'bob', 20)}
    ${breakdownRow('', '', 'carol', 12)}
  </div>
</section>`;

const statRowHtml = `
<div id="inventoryStats" class="inventory-stats">
  <div class="stat-card"><div class="stat-card__label">Online</div><div class="stat-card__value">7</div></div>
  <div class="stat-card"><div class="stat-card__label">Evolved Stones</div><div class="stat-card__value">312</div></div>
  <div class="stat-card"><div class="stat-card__label">Secret Fish</div><div class="stat-card__value">41</div></div>
  <div class="stat-card"><div class="stat-card__label">Forgotten Fish</div><div class="stat-card__value">9</div></div>
  <div class="stat-card stat-card--ruby">
    <div class="stat-card__label"><span class="stat-card__icon stat-card__icon--ruby"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 3h10l4 6-9 12L3 9l4-6zm.7 2L5.3 8.3h13.4L16.3 5H7.7zM5.6 10l6.4 8.6L18.4 10H5.6z"/></svg></span>Ruby Gemstone</div>
    <div class="stat-card__value" id="statRubyGemstone">23</div>
  </div>
</div>`;

const runicCardHtml = `
<div class="ft-card ft-card--stone" style="max-width:230px">
  <div class="ft-card-icon"><img src="${runicUrl}" alt="Runic Stone" width="54" height="54"></div>
  <div class="ft-card-main"><div class="ft-card-name">Runic Stone</div></div>
</div>`;

const removeMenuHtml = `
<div class="player-control-bar" style="position:relative;z-index:40">
  <input type="text" value="Add username..." readonly>
  <button class="btn btn-add">Add</button>
  <div class="remove-dropdown" style="z-index:60">
    <button class="btn btn-remove" type="button">Remove</button>
    <div class="remove-dropdown__menu" style="z-index:300">
      <button class="remove-dropdown__item"><span>alice</span></button>
      <button class="remove-dropdown__item"><span>bob</span></button>
      <button class="remove-dropdown__item"><span>carol</span></button>
      <div class="remove-dropdown__divider"></div>
      <button class="remove-dropdown__item remove-dropdown__item--all"><span>Remove all usernames</span></button>
    </div>
  </div>
</div>
<table class="accounts-table" style="width:100%;margin-top:8px">
  <thead><tr><th>Username</th><th>Fish</th><th>Stones</th></tr></thead>
  <tbody>
    <tr><td class="accounts-table__username">alice</td><td>120</td><td>44</td></tr>
    <tr><td class="accounts-table__username">bob</td><td>98</td><td>31</td></tr>
    <tr><td class="accounts-table__username">carol</td><td>76</td><td>27</td></tr>
  </tbody>
</table>`;

function page(inner, opts = {}) {
  const pad = opts.pad == null ? 24 : opts.pad;
  return `<!doctype html><html><head><meta charset="utf-8">
<style>${css}</style>
<style>
  body { margin:0; background:#0b1220; color:#e2e8f0; font-family:-apple-system,Segoe UI,Roboto,sans-serif; }
  .proof-wrap { padding:${pad}px; }
  .inventory-stats { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; }
</style></head>
<body class="${opts.bodyClass || ''}"><div class="proof-wrap">${inner}</div></body></html>`;
}

async function setContent(page, html) {
  await page.setContent(html, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(150);
}

async function shot(page, el, file) {
  const target = el ? await page.$(el) : page;
  await target.screenshot({ path: path.join(OUT_DIR, file), timeout: 15000 });
  return file;
}

(async () => {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  const captured = [];

  // Desktop captures
  const desk = await browser.newPage({ viewport: { width: 1120, height: 1000 }, deviceScaleFactor: 2 });

  await setContent(desk, page(fishDetailHtml));
  captured.push(await shot(desk, '.ft-detail-panel', 'fix-fish-inline-detail.png'));

  await setContent(desk, page(totemDetailHtml));
  captured.push(await shot(desk, '.ft-detail-panel', 'fix-item-inline-detail.png'));

  await setContent(desk, page(statRowHtml));
  captured.push(await shot(desk, '#inventoryStats', 'fix-ruby-gemstone-stat.png'));

  await setContent(desk, page(runicCardHtml));
  captured.push(await shot(desk, '.ft-card', 'fix-runic-stone-image.png'));

  await desk.close();

  // Mobile capture for the remove dropdown over the table/list
  const mob = await browser.newPage({ viewport: { width: 390, height: 520 }, deviceScaleFactor: 2 });
  await setContent(mob, page(removeMenuHtml, { bodyClass: '', pad: 14 }));
  // Capture the whole viewport so the absolutely-positioned dropdown menu
  // (which overflows the control bar box) is fully visible above the table.
  captured.push(await shot(mob, null, 'fix-mobile-remove-dropdown.png'));
  await mob.close();

  await browser.close();

  // Index page linking the screenshots
  const indexHtml = page(
    '<h1>STRICT TRACKER FIX — visual proof</h1>'
    + captured.map((f) => `<h3>${f}</h3><img src="./${f}" style="max-width:100%;border:1px solid #334155;border-radius:8px">`).join(''),
  );
  fs.writeFileSync(path.join(OUT_DIR, 'tracker-fix-2026-06-15.html'), indexHtml);
  // eslint-disable-next-line no-console
  console.log('PROOF_OK', captured.join(', '));
})().catch((e) => { console.error(e); process.exit(1); });
