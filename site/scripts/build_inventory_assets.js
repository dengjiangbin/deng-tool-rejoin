'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const EJS_PATH = path.join(ROOT, 'views', 'fishit_tracker.ejs');
const SOURCE_PATH = path.join(ROOT, 'src', 'inventory', 'fishit_tracker.source.ejs');
const OUT_DIR = path.join(ROOT, 'public', 'assets');
const MANIFEST_PATH = path.join(ROOT, 'src', 'inventoryAssetManifest.json');
const MARKER = 'TRACKER_COUNTS_AND_10S_POLL_FIX_2026_06_14';

const trackerRarityStyle = require('../src/fishitTrackerRarityStyle');
const fishitStoneDisplayMap = require('../src/fishitStoneDisplayMap');
const trackerItemImageOverrides = require('../src/fishitTrackerItemImageOverrides');

function minifyCss(source) {
  return String(source || '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\s+/g, ' ')
    .replace(/\s*([{}:;,>+~])\s*/g, '$1')
    .trim();
}

function minifyJs(source) {
  return String(source || '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\r\n/g, '\n')
    .replace(/\n+/g, '\n')
    .trim();
}

function hashContent(content) {
  return crypto.createHash('sha256').update(content).digest('hex').slice(0, 12);
}

function extractBlock(source, openTag, closeTag) {
  const start = source.indexOf(openTag);
  const end = source.indexOf(closeTag, start + openTag.length);
  if (start < 0 || end < 0) throw new Error(`Missing ${openTag} block`);
  return {
    before: source.slice(0, start),
    inner: source.slice(start + openTag.length, end),
    after: source.slice(end + closeTag.length),
  };
}

function transformJs(rawJs) {
  let js = rawJs;
  js = js.replace(/^\s*\(function \(\) \{\s*\r?\n\s*'use strict';\s*\r?\n/m, '');
  js = js.trim();
  if (js.endsWith('}());')) js = js.slice(0, -5).trimEnd();
  js = js.replace(
    /const DEBUG_INVENTORY = <%= \(typeof debugInventory !== 'undefined' && debugInventory\) \? 'true' : 'false' %>;/,
    'const DEBUG_INVENTORY = !!__CFG__.debugInventory;',
  );
  js = js.replace(
    /const APK_EMBED = <%= \(typeof apkEmbed !== 'undefined' && apkEmbed\) \? 'true' : 'false' %>;/,
    'const APK_EMBED = !!__CFG__.apkEmbed;',
  );
  js = js.replace(
    /const INITIAL_USERNAME = <%- JSON\.stringify\(typeof initialUsername !== 'undefined' \? initialUsername : ''\) %>;/,
    'const INITIAL_USERNAME = __CFG__.initialUsername || \'\';',
  );
  js = js.replace(
    /const CSRF_CFG = <%-[\s\S]*?%>;\s*function readRuntimeCsrfToken\(\) \{[\s\S]*?\}\s*const CSRF_TOKEN = readRuntimeCsrfToken\(\);/,
    "const CSRF_CFG = __CFG__.csrfToken || '';\n  function readRuntimeCsrfToken(){if(CSRF_CFG)return CSRF_CFG;try{const hidden=document.querySelector('input[name=\\\"_csrf\\\"]');if(hidden&&hidden.value)return hidden.value;}catch(_){}return '';}\n  const CSRF_TOKEN = readRuntimeCsrfToken();",
  );
  js = js.replace(
    /const CSRF_TOKEN = <%-[\s\S]*?%>;/,
    "const CSRF_CFG = __CFG__.csrfToken || '';\n  function readRuntimeCsrfToken(){if(CSRF_CFG)return CSRF_CFG;try{const hidden=document.querySelector('input[name=\\\"_csrf\\\"]');if(hidden&&hidden.value)return hidden.value;}catch(_){}return '';}\n  const CSRF_TOKEN = readRuntimeCsrfToken();",
  );
  js = js.replace(
    /const TRACKER_UI_DEPLOY = '<%= typeof trackerUiDeployMarker !== "undefined" \? trackerUiDeployMarker : "[^"]+" %>';/,
    'const TRACKER_UI_DEPLOY = __CFG__.trackerUiDeployMarker || \'\';',
  );
  js = js.replace(/^\s*<%- typeof trackerRarityJsBootstrap[\s\S]*?%>\s*$/m, trackerRarityStyle.buildTrackerRarityJsBootstrap());
  js = js.replace(/^\s*<%- typeof trackerStoneJsBootstrap[\s\S]*?%>\s*$/m, fishitStoneDisplayMap.buildTrackerStoneJsBootstrap());
  js = js.replace(/^\s*<%- typeof trackerItemImageJsBootstrap[\s\S]*?%>\s*$/m, trackerItemImageOverrides.buildTrackerItemImageOverridesJsBootstrap());
  js = js.replace(
    /\|\| '<%- typeof trackerLoadstring[\s\S]*?%>';/,
    '|| (__CFG__.trackerLoadstring || \'\');',
  );
  js = js.replace(
    /const RENDER_BUILD  = DEBUG_INVENTORY[\s\S]*?const PUBLIC_API_BUILD = DEBUG_INVENTORY[\s\S]*?: '';/,
    'const RENDER_BUILD = DEBUG_INVENTORY ? (__CFG__.renderBuild || \'\') : \'\';\n  const PUBLIC_API_BUILD = DEBUG_INVENTORY ? (__CFG__.publicApiBuild || \'\') : \'\';',
  );
  if (/<%|<%-|<%=/.test(js)) {
    throw new Error('inventory asset build left unresolved EJS in JS output');
  }
  return `(function(){'use strict';function readInventoryCfg(){const el=document.getElementById('inventory-runtime');if(!el)return{};try{return JSON.parse(el.textContent||'{}');}catch(_){return{};}}const __CFG__=readInventoryCfg();\n${js}\n}());`;
}

function transformCss(rawCss) {
  const withoutRarityEjs = rawCss.replace(
    /<%= typeof trackerRarityCardCss !== 'undefined' \? trackerRarityCardCss : '' %>\s*/,
    '',
  );
  return `${withoutRarityEjs}\n${trackerRarityStyle.buildFtCardRarityCss()}`;
}

function buildShellEjs(beforeStyle, bodyHtml, marker) {
  const cleanedBefore = beforeStyle
    .replace(/<!--[\s\S]*?-->\s*/g, '')
    .replace(
      /data-tracker-ui-deploy="[^"]+"/,
      'data-tracker-ui-deploy="<%= typeof trackerUiDeployMarker !== \'undefined\' ? trackerUiDeployMarker : \'' + marker + '\' %>"',
    )
    .replace(
      /content="<%= typeof trackerUiDeployMarker[^"]+"/,
      'content="<%= typeof trackerUiDeployMarker !== \'undefined\' ? trackerUiDeployMarker : \'' + marker + '\' %>"',
    )
    .replace(/<\/head>\s*$/i, '')
    .trimEnd();
  const cleanedBody = bodyHtml.replace(/^\s*<\/head>\s*/i, '').trim();
  return `${cleanedBefore}
  <link rel="stylesheet" href="/public/css/logoutConfirm.css?v=<%= typeof assetVersion !== 'undefined' ? assetVersion : '' %>">
  <link rel="stylesheet" href="/public/css/app-sidebar.css?v=<%= typeof assetVersion !== 'undefined' ? assetVersion : '' %>">
  <link rel="stylesheet" href="<%= inventoryAssetCssUrl %>">
</head>
${cleanedBody}
<script type="application/json" id="inventory-runtime"><%- JSON.stringify(inventoryRuntimeConfig).replace(/</g, '\\u003c') %></script>
<script src="/public/js/count-up-stats.js?v=<%= typeof assetVersion !== 'undefined' ? assetVersion : '' %>" defer></script>
<script src="/public/js/logoutConfirm.js?v=<%= typeof assetVersion !== 'undefined' ? assetVersion : '' %>" defer></script>
<script src="<%= inventoryAssetJsUrl %>" defer></script>
</html>
`;
}

function main() {
  const sourcePath = fs.existsSync(SOURCE_PATH) ? SOURCE_PATH : EJS_PATH;
  const source = fs.readFileSync(sourcePath, 'utf8');
  const styleBlock = extractBlock(source, '<style>', '</style>');
  const scriptBlock = extractBlock(styleBlock.after, '<script>', '</script>');
  const bodyHtml = scriptBlock.before.trim();
  const css = minifyCss(transformCss(styleBlock.inner));
  const js = minifyJs(transformJs(scriptBlock.inner));
  const cssHash = hashContent(css);
  const jsHash = hashContent(js);
  const cssName = `inventory.${cssHash}.css`;
  const jsName = `inventory.${jsHash}.js`;
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(path.join(OUT_DIR, cssName), css);
  fs.writeFileSync(path.join(OUT_DIR, jsName), js);
  const manifest = {
    marker: MARKER,
    css: cssName,
    js: jsName,
    cssHash,
    jsHash,
    builtAt: new Date().toISOString(),
  };
  fs.writeFileSync(MANIFEST_PATH, `${JSON.stringify(manifest, null, 2)}\n`);
  const shell = buildShellEjs(styleBlock.before, bodyHtml, MARKER);
  fs.writeFileSync(EJS_PATH, shell);
  try {
    require('../src/fishitTrackerTopGridAssets').syncTopGridAssets({ persist: true });
  } catch (err) {
    console.warn('[inventory-assets] tracker top-grid asset sync skipped:', err && err.message ? err.message : err);
  }
  console.log('[inventory-assets] wrote', cssName, jsName, 'manifest', MANIFEST_PATH);
}

main();
