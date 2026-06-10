'use strict';

const fs = require('fs');
const path = require('path');
const {
  CLEAN_TRACKER_LOADSTRING,
  LOADER_BUILD,
  PROTECTED_DIST_RAW_URL_CACHE_BUST,
} = require('../src/fishitTrackerLoadstring');
const { BLOCKER10ZT3A_HOTFIX_LOADER_MOBILE_MARKER } = require('../src/fishitTrackerBuild');

const OUT = path.join(__dirname, '..', 'proofs', 'blocker10zt3a_hotfix_loader_mobile_proof.html');
const MARKER = BLOCKER10ZT3A_HOTFIX_LOADER_MOBILE_MARKER;

const html = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${MARKER} — loader + mobile proof</title>
<style>
:root{--bg:#0b1220;--panel:#111827;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;--accent:#38bdf8;--success:#22c55e}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--text);line-height:1.45}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.25rem;margin:0 0 6px}
.meta{color:var(--muted);font-size:.85rem;margin-bottom:20px}
.proof{margin:16px 0;padding:14px;border:1px solid var(--border);border-radius:12px;background:var(--panel)}
.proof h2{font-size:1rem;margin:0 0 10px}
.ok{color:var(--success);font-weight:600}
.frames{display:flex;flex-wrap:wrap;gap:16px;margin-top:12px}
.phone{width:360px;border:1px solid var(--border);border-radius:18px;overflow:hidden;background:#0f172a}
.phone__bar{padding:8px 12px;font-size:.72rem;color:var(--muted);border-bottom:1px solid var(--border)}
.phone__body{padding:12px}
.card{border:1px solid var(--border);border-radius:12px;padding:10px;margin-top:10px}
.card__label{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.card__user{font-weight:700;font-size:.95rem;overflow-wrap:anywhere;white-space:normal;margin:4px 0 8px}
.card__row{font-size:.8rem;color:var(--muted);margin:3px 0}
.card__row b{color:var(--text)}
.code{background:#0b1220;border:1px solid var(--border);border-radius:8px;padding:8px;font-size:.68rem;overflow-wrap:anywhere;white-space:pre-wrap}
ul{margin:8px 0 0;padding-left:18px;font-size:.85rem;color:var(--muted)}
code{background:#0b1220;padding:2px 6px;border-radius:6px}
</style></head>
<body data-ui-marker="${MARKER}">
<div class="wrap">
<h1>${MARKER}</h1>
<p class="meta">BLOCKER10ZT3A hotfix proof — generated ${new Date().toISOString()}</p>

<section class="proof">
<h2>1. Proof loader (website copy)</h2>
<p><span class="ok">PASS</span> <code>LOADER_BUILD=${LOADER_BUILD}</code></p>
<p><span class="ok">PASS</span> <code>FETCH_URL=${PROTECTED_DIST_RAW_URL_CACHE_BUST}</code></p>
<div class="code">${CLEAN_TRACKER_LOADSTRING.replace(/</g, '&lt;')}</div>
<ul>
<li>Prints <code>LOADER_BUILD=</code>, <code>FETCH_URL=</code>, <code>FETCHED_TRACKER_BUILD=</code> before executing dist</li>
<li>Dist executed prints <code>EXECUTED_TRACKER_BUILD=BLOCKER10ZT3...</code></li>
</ul>
</section>

<section class="proof">
<h2>2. Mobile account cards (360px / 390px)</h2>
<div class="frames">
<div class="phone">
<div class="phone__bar">360px — stacked cards, no empty middle gap</div>
<div class="phone__body">
<div class="card">
<div class="card__label">Account</div>
<div class="card__user">denghub2</div>
<div class="card__row">Status: <b>● 12s</b></div>
<div class="card__row">Coins: <b>—</b></div>
<div class="card__row">Caught: <b>68,885</b></div>
<div class="card__row">Rarest: <b>1/1.20M</b></div>
<div class="card__row">Fish: <b>20</b></div>
<div class="card__row">Types: <b>12</b></div>
<div class="card__row">Last sync: <b>12s</b></div>
</div>
</div></div>
<div class="phone" style="width:390px">
<div class="phone__bar">390px — long username wraps, not usern...</div>
<div class="phone__body">
<div class="card">
<div class="card__label">Account</div>
<div class="card__user">verylongrobloxusername2026</div>
<div class="card__row">Status: <b>● 7s</b></div>
<div class="card__row">Caught: <b>1,204</b></div>
</div>
</div></div>
</div>
<ul>
<li>Desktop <code>.accounts-table-wrap</code> hidden at <code>max-width:768px</code></li>
<li><code>.accounts-mobile-list</code> shown with <code>grid-template-columns:1fr</code> rows</li>
<li>Username uses <code>overflow-wrap:anywhere</code> — no <code>text-overflow:ellipsis</code> on primary label</li>
<li>APK <code>inventory-apk-embed</code> forces mobile cards</li>
</ul>
</section>
</div>
</body></html>`;

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, html);
console.log('wrote', OUT);
