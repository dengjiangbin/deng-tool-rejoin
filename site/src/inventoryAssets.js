'use strict';

const fs = require('fs');
const path = require('path');

const MANIFEST_PATH = path.join(__dirname, 'inventoryAssetManifest.json');

function loadManifest() {
  try {
    return JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
  } catch {
    return null;
  }
}

function inventoryAssetUrls() {
  const manifest = loadManifest();
  if (!manifest || !manifest.css || !manifest.js) {
    return {
      cssUrl: '/public/assets/inventory.fallback.css',
      jsUrl: '/public/assets/inventory.fallback.js',
      marker: 'inventory_assets_missing',
    };
  }
  return {
    cssUrl: `/public/assets/${manifest.css}`,
    jsUrl: `/public/assets/${manifest.js}`,
    marker: manifest.marker || 'inventory_assets',
    cssHash: manifest.cssHash,
    jsHash: manifest.jsHash,
  };
}

module.exports = {
  loadManifest,
  inventoryAssetUrls,
};
