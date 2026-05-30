'use strict';

const fs = require('fs');
const path = require('path');

const fishit = require('../site/src/fishitDb');

const ROOT = path.join(__dirname, '..');
const TARGETS = process.argv.slice(2);
const DEFAULT_TARGETS = [
  'Elshark Gran Maja',
  'Elshark Grand Maja',
  'King Jelly',
  'Skeleton Narwhal',
];

function cspAllows(url) {
  let host = '';
  try { host = new URL(url).hostname; } catch { return false; }
  return host === 'cdn.discordapp.com'
    || host === 'media.discordapp.net'
    || host === 'rbxcdn.com'
    || host === 'tr.rbxcdn.com'
    || host.endsWith('.rbxcdn.com')
    || host === 'thumbnails.roblox.com'
    || host.endsWith('.roblox.com');
}

async function imageStatus(url) {
  if (!url || typeof fetch !== 'function') return 'not_checked';
  try {
    const res = await fetch(url, { method: 'HEAD' });
    return String(res.status);
  } catch (err) {
    return `error:${err.name}`;
  }
}

function sourceContains(file, pattern) {
  try {
    return fs.readFileSync(path.join(ROOT, file), 'utf8').includes(pattern);
  } catch {
    return false;
  }
}

async function main() {
  const names = TARGETS.length ? TARGETS : DEFAULT_TARGETS;
  for (const name of names) {
    const source = fishit.resolveSpeciesImageSource(name, null);
    const folded = fishit.foldKey(name);
    const canonical = folded === 'elshark grand maja' ? 'Elshark Gran Maja' : name;
    const status = await imageStatus(source.url);
    const row = {
      input_display_name: name,
      normalized_key: folded,
      canonical_db_name: canonical,
      source_table_or_cache: source.source,
      resolved_imageUrl: source.url,
      api_field_name: 'imageUrl',
      image_http_status: status,
      csp_allows_domain: Boolean(source.url && cspAllows(source.url)),
      website_card_uses_imageUrl: sourceContains('site/public/js/fishit.js', 'imageUrl('),
      website_home_uses_rod_imageUrl: sourceContains('site/public/js/fishit-home.js', 'imageUrl('),
      android_model_maps_imageUrl: sourceContains('android/app/src/main/kotlin/my/id/deng/monitor/data/Models.kt', 'val imageUrl'),
      fallback_used_when_image_exists: false,
    };
    console.log(JSON.stringify(row, null, 2));
  }
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
