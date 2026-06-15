'use strict';

// Visual proof for the inline detail INSTANCE cards (2026-06-15). Renders the
// completed King Crab detail view using the REAL compiled inventory CSS and the
// EXACT shipped renderFishInstanceCard markup, then screenshots:
//   1. full detail (6 King Crab cards: image + mutation + weight each + search)
//   2. search filtered to "Gold"
//   node scripts/gen_tracker_instance_detail_proof.js

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.join(__dirname, '..');
const manifest = require('../src/inventoryAssetManifest.json');
const CSS_PATH = path.join(ROOT, 'public', 'assets', manifest.css);
const OUT_DIR = path.join(ROOT, 'public', 'proof');

const css = fs.readFileSync(CSS_PATH, 'utf8');

// Mirror of the shipped FT_MUTATION_COLORS / ftMutationColor.
const FT_MUTATION_COLORS = {
  gold: '#fbbf24', golden: '#fbbf24', ruby: '#f87171', diamond: '#7dd3fc',
  emerald: '#34d399', sapphire: '#60a5fa', shiny: '#fde68a', glowing: '#fde68a',
  radiant: '#fde68a', frozen: '#67e8f9', ice: '#67e8f9', icy: '#67e8f9',
  glacial: '#67e8f9', corrupt: '#c084fc', corrupted: '#c084fc', void: '#c084fc',
  dark: '#c084fc', rainbow: '#f0abfc', galaxy: '#a78bfa', cosmic: '#a78bfa',
  fire: '#fb923c', molten: '#fb923c', lava: '#fb923c', normal: '#94a3b8',
};
function ftMutationColor(mut) {
  const key = String(mut || '').toLowerCase().trim();
  if (!key) return '';
  return FT_MUTATION_COLORS[key] || '#cbd5e1';
}
function escHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// EXACT copy of the shipped renderFishInstanceCard markup.
function renderFishInstanceCard(card) {
  const mutColor = ftMutationColor(card.mutation);
  const mut = card.mutation
    ? `<div class="ft-inst-card__mut" style="color:${escHtml(mutColor)}">${escHtml(card.mutation)}</div>`
    : '<div class="ft-inst-card__mut" aria-hidden="true"></div>';
  const img = card.imgSrc
    ? `<div class="ft-inst-card__img"><img src="${escHtml(card.imgSrc)}" alt="${escHtml(card.name)}" decoding="async"></div>`
    : '<div class="ft-inst-card__img" aria-hidden="true">&#x1F41F;</div>';
  const weight = card.weight
    ? `<div class="ft-inst-card__weight">Weight: ${escHtml(card.weight)}</div>`
    : '<div class="ft-inst-card__weight ft-inst-card__weight--unknown">Weight unknown</div>';
  return `<div class="ft-inst-card">${img}<div class="ft-inst-card__body">${mut}<div class="ft-inst-card__name">${escHtml(card.name)}</div><div class="ft-inst-card__owner">${escHtml(card.owner || '-')}</div>${weight}</div></div>`;
}

function ftFilterInstanceCards(cards, query) {
  const q = String(query || '').toLowerCase().trim();
  if (!q) return cards;
  return cards.filter((c) => {
    const name = String(c.name || '').toLowerCase();
    const mut = String(c.mutation || '').toLowerCase();
    return name.includes(q) || mut.includes(q) || `${mut} ${name}`.includes(q);
  });
}

// Prefer a real decodable cached fish image; fall back to an inline SVG crab so
// the proof always shows a rendered image on every card.
// Representative fish image for the static proof. The live page feeds card.imgSrc
// from the SAME resolver as the overview fish cards; here we use an inline SVG so
// every card slot shows a clear rendered image offline.
function pickFishImage() {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>`
    + `<rect width='64' height='64' rx='12' fill='#1f2937'/>`
    + `<g fill='#fb7185'><ellipse cx='32' cy='38' rx='15' ry='11'/>`
    + `<circle cx='20' cy='24' r='6'/><circle cx='44' cy='24' r='6'/>`
    + `<rect x='8' y='40' width='12' height='4' rx='2' transform='rotate(20 14 42)'/>`
    + `<rect x='44' y='40' width='12' height='4' rx='2' transform='rotate(-20 50 42)'/></g>`
    + `<circle cx='26' cy='36' r='2.4' fill='#0b1220'/><circle cx='38' cy='36' r='2.4' fill='#0b1220'/></svg>`;
  return { src: `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`, kind: 'inline SVG crab' };
}

const picked = pickFishImage();
const img = picked.src;
const cards = [
  { owner: 'denghub2', mutation: 'Gold', name: 'King Crab', weight: '9.3 kg', imgSrc: img },
  { owner: 'denghub2', mutation: 'Gold', name: 'King Crab', weight: '7.8 kg', imgSrc: img },
  { owner: 'denghub2', mutation: 'Shiny', name: 'King Crab', weight: '6 kg', imgSrc: img },
  { owner: 'denghub2', mutation: '', name: 'King Crab', weight: '5.2 kg', imgSrc: img },
  { owner: 'denghub2', mutation: '', name: 'King Crab', weight: '4.1 kg', imgSrc: img },
  { owner: 'denghub2', mutation: '', name: 'King Crab', weight: '', imgSrc: img },
];

function panelHtml(list, query) {
  const filtered = ftFilterInstanceCards(list, query);
  const body = filtered.length
    ? filtered.map(renderFishInstanceCard).join('')
    : '<div class="ft-detail-owner ft-detail-owner--empty">No matching fish instances</div>';
  return `
<section class="ft-detail-panel">
  <div class="ft-detail-panel__head">
    <button type="button" class="ft-detail-back"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="15 18 9 12 15 6"></polyline></svg>Back</button>
    <div class="ft-detail-icon">${img ? `<img src="${img}" alt="King Crab">` : '<span class="ft-detail-icon__fallback">&#x1F980;</span>'}</div>
    <div class="ft-detail-headtext">
      <div class="ft-detail-name">King Crab</div>
      <div class="ft-detail-count">${list.length} fish</div>
    </div>
  </div>
  <div class="ft-detail-search"><input class="ft-detail-search__input" value="${escHtml(query || '')}" placeholder="Search by name or mutation..."></div>
  <div class="ft-detail-instances">${body}</div>
</section>`;
}

function pageHtml(inner) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
  :root{--font:'Segoe UI',system-ui,sans-serif;--bg:#0a0e16;--surface:#121826;--surface2:#0e131f;--border:#1e2738;--text:#e2e8f0;--muted:#94a3b8;--accent2:#60a5fa;--radius:12px;}
  body{margin:0;background:var(--bg);font-family:var(--font);color:var(--text);padding:22px;}
  ${css}
  .ft-detail-panel{max-width:1040px;margin:0 auto;}
  </style></head><body><div class="inventory-shell">${inner}</div></body></html>`;
}

const log = (m) => process.stdout.write(`[stage] ${m}\n`);
(async () => {
  const hardTimeout = setTimeout(() => { console.error('HARD_TIMEOUT'); process.exit(3); }, 60000);
  hardTimeout.unref();
  fs.mkdirSync(OUT_DIR, { recursive: true });
  log('launching');
  const browser = await chromium.launch({
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--no-proxy-server',
      '--disable-extensions', '--disable-background-networking'],
  });
  try {
    log('launched');
    const context = await browser.newContext({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    log('newPage');

    await page.setContent(pageHtml(panelHtml(cards, '')), { waitUntil: 'domcontentloaded' });
    log('setContent full');
    const full = path.join(OUT_DIR, 'instance-detail-full.png');
    await page.screenshot({ path: full, fullPage: true });
    log('screenshot full');

    await page.setContent(pageHtml(panelHtml(cards, 'Gold')), { waitUntil: 'domcontentloaded' });
    const search = path.join(OUT_DIR, 'instance-detail-search-gold.png');
    await page.screenshot({ path: search, fullPage: true });
    log('screenshot search');
    clearTimeout(hardTimeout);

    console.log('INSTANCE_DETAIL_PROOF OK');
    console.log('  image_used:', picked.kind);
    console.log('  full:', full);
    console.log('  search:', search);
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error('PROOF FAILED:', e.message); process.exit(1); });
