'use strict';

const fs = require('fs');
const path = require('path');
const fishit = require('../site/src/fishitDb');

const args = process.argv.slice(2);
const channel = args.includes('--channel') ? args[args.indexOf('--channel') + 1] : '1481530183545520159';
const outPath = path.join(__dirname, '..', 'site', 'data', 'fishit_image_assets.json');
const targets = ['Elshark Gran Maja', 'Elshark Grand Maja', 'King Jelly', 'Skeleton Narwhal'];

async function status(url) {
  if (!url || typeof fetch !== 'function') return 'not_checked';
  try {
    const res = await fetch(url, { method: 'HEAD' });
    return String(res.status);
  } catch (err) {
    return `error:${err.name}`;
  }
}

async function main() {
  const rows = [];
  const cache = {
    source: 'leaderboard_db_source',
    channel_id: channel,
    leaderboard_resolver: 'C:\\Users\\Administrator\\Desktop\\DENG Fish It\\utils\\fishImageResolver.js',
    generated_at: new Date().toISOString(),
    images: {},
  };

  for (const name of targets) {
    const source = fishit.resolveSpeciesImageSource(name, null);
    const canonical = fishit.foldKey(name) === 'elshark grand maja' ? 'Elshark Gran Maja' : name;
    cache.images[name] = {
      canonical_name: canonical,
      imageUrl: source.url,
      source: source.source,
    };
    rows.push({
      input: name,
      canonical_name: canonical,
      channel_id: channel,
      scanned_live_channel: false,
      reason_live_channel_not_scanned: process.env.DISCORD_TOKEN ? null : 'DISCORD_TOKEN not available to this script',
      leaderboard_db_source_used: true,
      source: source.source,
      imageUrl: source.url,
      http_status: await status(source.url),
    });
  }

  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, `${JSON.stringify(cache, null, 2)}\n`, 'utf8');
  for (const row of rows) console.log(JSON.stringify(row, null, 2));
}

main().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
