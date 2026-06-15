'use strict';

// Generates a self-contained static proof page for the /tracker polish task.
// It links the REAL compiled inventory CSS (from the asset manifest) and renders
// mock markup for: the 5-card stat row (Ruby Gemstone), 2-column fish/stone/totem
// grids, the clickable detail modal (mutation above clean name + owners sorted by
// username), and the "Remove all usernames" confirmation modal.
//
// Open the output in a browser to capture screenshots (desktop + mobile widths).

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const manifest = require(path.join(ROOT, 'src', 'inventoryAssetManifest.json'));
const OUT_DIR = path.join(ROOT, 'public', 'proof');
const OUT_PATH = path.join(OUT_DIR, 'tracker-polish-2026-06-15.html');

const cssHref = `/public/assets/${manifest.css}`;

function statRow() {
  return `
  <div class="inventory-stats" id="inventoryStats" aria-label="Inventory summary">
    <div class="stat-card stat-card--online"><div class="stat-card__label">Online / Accounts</div><div class="stat-card__value">1 / 3</div></div>
    <div class="stat-card stat-card--stones"><div class="stat-card__label">Evolved Enchant Stone</div><div class="stat-card__value">12</div></div>
    <div class="stat-card stat-card--secret"><div class="stat-card__label">Secret Fish</div><div class="stat-card__value">11</div></div>
    <div class="stat-card stat-card--forgotten"><div class="stat-card__label">Forgotten Fish</div><div class="stat-card__value">3</div></div>
    <div class="stat-card stat-card--ruby"><div class="stat-card__label"><span class="stat-card__icon stat-card__icon--ruby"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 3h10l4 6-9 12L3 9l4-6zm.7 2L5.3 8.3h13.4L16.3 5H7.7zM5.6 10l6.4 8.6L18.4 10H5.6z"/></svg></span>Ruby Gemstone</div><div class="stat-card__value">5</div></div>
  </div>`;
}

function fishCard(name, qty, owners, rarity) {
  return `
  <div class="ft-card ft-card--fish ft-card--interactive" data-rarity="${rarity || 'common'}" role="button" tabindex="0">
    <div class="ft-card-icon"><img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='54' height='54'%3E%3Crect width='54' height='54' rx='8' fill='%23334155'/%3E%3C/svg%3E" alt=""></div>
    <div class="ft-card-main">
      <div class="ft-card-name" title="${name}">${name}</div>
      <div class="ft-card-stats"><span class="ft-chip">&#128101; ${owners}</span><span class="ft-chip ft-chip-qty">x${qty}</span><span class="ft-chip ft-chip-rarity">${rarity || 'Common'}</span></div>
    </div>
  </div>`;
}

function stoneCard(name, qty, owners) {
  return `
  <div class="ft-card ft-card--stone ft-card--interactive" role="button" tabindex="0">
    <div class="ft-card-icon"><img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='48' height='48'%3E%3Crect width='48' height='48' rx='8' fill='%234b5563'/%3E%3C/svg%3E" alt=""></div>
    <div class="ft-card-main">
      <div class="ft-card-name" title="${name}">${name}</div>
      <div class="ft-card-stats"><span class="ft-chip">&#128101; ${owners}</span><span class="ft-chip ft-chip-qty">x${qty}</span></div>
    </div>
  </div>`;
}

function section(title, cards, gridClass) {
  return `
  <div class="items-subsection">
    <div class="items-subsection__title">${title}</div>
    <div class="items-grid inventory-grid ${gridClass}">${cards}</div>
  </div>`;
}

function detailModalOpen() {
  return `
  <div class="ft-detail-overlay" style="position:relative;inset:auto;min-height:360px;">
    <div class="ft-detail-dialog">
      <button type="button" class="ft-detail-close" aria-label="Close">&times;</button>
      <div class="ft-detail-head">
        <div class="ft-detail-icon"><span class="ft-detail-icon__fallback">&#128142;</span></div>
        <div class="ft-detail-headtext">
          <div class="ft-detail-tag">[Gemstone]</div>
          <div class="ft-detail-name">Ruby</div>
          <div class="ft-detail-meta"><span class="ft-detail-chip">Mythic</span><span class="ft-detail-chip">Total x5</span></div>
        </div>
      </div>
      <div class="ft-detail-owners-title">Owned by</div>
      <div class="ft-detail-owners">
        <div class="ft-detail-owner"><span class="ft-detail-owner__name">accountA</span><span class="ft-detail-owner__qty">x2</span></div>
        <div class="ft-detail-owner"><span class="ft-detail-owner__name">dengjiangbin</span><span class="ft-detail-owner__qty">x2</span></div>
        <div class="ft-detail-owner"><span class="ft-detail-owner__name">zUser</span><span class="ft-detail-owner__qty">x1</span></div>
      </div>
    </div>
  </div>`;
}

function removeAllModalOpen() {
  return `
  <div class="modal-overlay" style="position:relative;inset:auto;min-height:240px;">
    <div class="modal-dialog">
      <h2 class="modal-title">Remove all usernames?</h2>
      <p class="modal-helper">This will remove every username from your tracker list. This will not delete your Discord account.</p>
      <div class="modal-actions">
        <button type="button" class="btn-modal-cancel">Cancel</button>
        <button type="button" class="btn-modal-submit btn-modal-submit--danger">Remove All</button>
      </div>
    </div>
  </div>`;
}

function panel(label, widthCss, bodyHtml, apk) {
  return `
  <h2 style="margin:28px 0 10px;font-size:15px;color:#93c5fd;">${label}</h2>
  <div class="${apk ? 'inventory-apk-embed' : ''}" style="${widthCss};margin:0 auto;border:1px solid #2a3040;border-radius:14px;padding:16px;background:#0d0f14;">
    ${bodyHtml}
  </div>`;
}

function build() {
  const fishCards = [
    fishCard('Great White Shark', 4, 2, 'secret'),
    fishCard('Forgotten Angelfish', 1, 1, 'forgotten'),
    fishCard('Ruby', 5, 3, 'mythic'),
    fishCard('Cactus Pufferfish', 9, 2, 'rare'),
  ].join('');
  const stoneCards = [
    stoneCard('Normal Enchant Stone', 1303, 1),
    stoneCard('Evolved Enchant Stone', 12, 2),
  ].join('');
  const totemCards = [
    stoneCard('Mutation Totem', 32, 1),
    stoneCard('Shiny Totem', 4, 2),
  ].join('');

  const desktopBody = statRow()
    + section('Fish', fishCards, 'fish-grid')
    + section('Enchant Stones (1,315)', stoneCards, 'stones-grid stone-grid')
    + section('Totems (36)', totemCards, 'totems-grid');

  const mobileBody = statRow()
    + section('Fish', fishCards, 'fish-grid')
    + section('Enchant Stones (1,315)', stoneCards, 'stones-grid stone-grid')
    + section('Totems (36)', totemCards, 'totems-grid');

  const html = `<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DENG /tracker polish proof — 2026-06-15</title>
<link rel="stylesheet" href="${cssHref}">
<style>body{background:#0d0f14;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;padding:24px;} .proof-note{color:#8a94a6;font-size:13px;margin-bottom:18px;} h1{font-size:20px;margin-bottom:6px;}</style>
</head>
<body>
<h1>DENG /tracker polish — visual proof</h1>
<p class="proof-note">Static render using the real compiled inventory CSS (<code>${manifest.css}</code>). Resize the browser to compare desktop (&gt;900px) vs mobile widths.</p>
${panel('A/B/C — Desktop (5 stat cards in one row, 2-col-ready grids, consistent sizing)', 'max-width:1040px', desktopBody, false)}
${panel('A/C/F — Mobile / APK shell (4 stat cards first row + Ruby second row, 2-column grids)', 'max-width:412px', mobileBody, true)}
${panel('E/G — Clickable card detail modal (mutation above clean name, owners sorted by username)', 'max-width:480px', detailModalOpen(), false)}
${panel('D — Remove all usernames confirmation', 'max-width:520px', removeAllModalOpen(), false)}
</body>
</html>
`;
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(OUT_PATH, html);
  console.log('[tracker-polish-proof] wrote', OUT_PATH, 'using css', manifest.css);
}

build();
