'use strict';
/**
 * Rod image resolver.
 *
 * The Fish It bot does not store rod artwork in its stats DB (only counts).
 * The real rod images live as Discord custom emoji in the rod channel
 * (KAITUN_CHANNEL_ID = 1483265484215287909). We mirror those resolved CDN URLs
 * in site/data/fishit_rod_assets.json so /api/fishit/* can serve real rod
 * images instead of a generic fallback icon.
 *
 * Nothing is invented: each entry maps a rod key to the bot's canonical
 * `BRANDING_EMOJI_IDS` snowflake and the cdn.discordapp.com/emojis/<id>.png URL.
 * If the config is missing/unreadable, resolvers return null so the client
 * shows its fallback icon (never a wrong image).
 */

const path = require('path');
const fs = require('fs');

const ASSET_PATH = process.env.FISHIT_ROD_ASSETS_PATH
  || path.join(__dirname, '..', 'data', 'fishit_rod_assets.json');

const TTL_MS = Number(process.env.FISHIT_ROD_ASSETS_TTL_MS || 60_000);

const DEFAULT_LABELS = {
  ghostfinn: 'Ghostfinn Rod',
  element: 'Element Rod',
  diamond: 'Diamond Rod',
};

let _cache = null;
let _at = 0;

function _isHttpUrl(u) {
  return typeof u === 'string' && /^https?:\/\//i.test(u.trim());
}

function loadRodAssets() {
  const now = Date.now();
  if (_cache && now - _at < TTL_MS) return _cache;
  let rods = {};
  try {
    if (fs.existsSync(ASSET_PATH)) {
      const parsed = JSON.parse(fs.readFileSync(ASSET_PATH, 'utf8'));
      if (parsed && parsed.rods && typeof parsed.rods === 'object') rods = parsed.rods;
    }
  } catch (err) {
    console.warn('[fishit] rod assets load failed:', err && err.message ? err.message : err);
    rods = {};
  }
  _cache = rods;
  _at = now;
  return rods;
}

/** Real rod image URL for a rod key (ghostfinn|element|diamond), or null. */
function rodImageUrl(key) {
  const k = String(key || '').toLowerCase().trim();
  const entry = loadRodAssets()[k];
  const u = entry && entry.imageUrl;
  return _isHttpUrl(u) ? String(u).trim() : null;
}

/** Display label for a rod key (falls back to a sane default). */
function rodLabel(key) {
  const k = String(key || '').toLowerCase().trim();
  const entry = loadRodAssets()[k];
  return (entry && typeof entry.label === 'string' && entry.label) || DEFAULT_LABELS[k] || k;
}

/** Test seam. */
function _resetCache() { _cache = null; _at = 0; }

module.exports = { ASSET_PATH, loadRodAssets, rodImageUrl, rodLabel, _resetCache };
